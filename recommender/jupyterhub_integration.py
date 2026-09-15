"""JupyterHub preview and spawn integration for pluggable recommenders.

The browser receives only a bounded recommendation and operational telemetry.
User intent, code context, and raw provider responses are used transiently and
are never stored in preview records, user options, logs, or pod metadata.
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from .deployment import DeploymentMetadata, PACKAGE_VERSION, validate_deployment_environment
from .base import Recommender
from .models import RecommendationRequest
from .policy import PolicyValidator
from .p2_backend import p2_requires_manual_override
from .p3_backend import p3_requires_manual_override
from .registry import create_recommender
from .reliability import AsyncRecommendationExecutor, RecommendationResult
from .rule_based import PROFILES, load_image_catalog


PREVIEW_VERSION = "recommendation-preview-v2"
PREVIEW_TTL_SECONDS = 1800
PREVIEW_MAX_ENTRIES = 1000
MAX_INTENT_CHARACTERS = 2000
MAX_CODE_CONTEXT_CHARACTERS = 5000

PROFILE_RESOURCES = {
    "small": {
        "cpu_guarantee": 0.1,
        "cpu_limit": 0.5,
        "mem_guarantee": "256M",
        "mem_limit": "384M",
    },
    "medium": {
        "cpu_guarantee": 0.5,
        "cpu_limit": 1,
        "mem_guarantee": "768M",
        "mem_limit": "1G",
    },
    "large": {
        "cpu_guarantee": 1.5,
        "cpu_limit": 2,
        "mem_guarantee": "1536M",
        "mem_limit": "2G",
    },
}


def safe_escape_truncate(value: object, max_len: int = 240) -> str:
    escaped = html.escape(str(value or ""))
    if len(escaped) <= max_len:
        return escaped
    return re.sub(r"&[a-zA-Z0-9#]*$", "", escaped[:max_len])


def safe_json_dumps(value: object) -> str:
    return (
        json.dumps(value, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("'", "\\u0027")
    )


def _username(subject: object) -> str:
    current_user = getattr(subject, "current_user", None)
    if isinstance(current_user, str) and current_user:
        return current_user
    current_name = getattr(current_user, "name", None)
    if isinstance(current_name, str) and current_name:
        return current_name
    user = getattr(subject, "user", None)
    if isinstance(user, str) and user:
        return user
    name = getattr(user, "name", None)
    if isinstance(name, str) and name:
        return name
    return "anonymous-user"


_DATASET_SIZE_PATTERN = re.compile(
    r"\b([0-9]+(?:\.[0-9]+)?)\s*(?:gb|gib)\b", re.IGNORECASE
)


def _extract_dataset_size_gb(text: str) -> float | None:
    match = _DATASET_SIZE_PATTERN.search(text)
    if match:
        try:
            val = float(match.group(1))
            if math.isfinite(val) and val >= 0:
                return val
        except (ValueError, TypeError):
            pass
    return None


def validate_preview_request(values: object) -> RecommendationRequest:
    if not isinstance(values, dict):
        raise ValueError("recommendation request must be a JSON object")
    if set(values) - {"intent", "dataset_size_gb", "code_context"}:
        raise ValueError("recommendation request contains unsupported fields")
    intent = values.get("intent", "")
    code_context = values.get("code_context", "")
    if not isinstance(intent, str) or not isinstance(code_context, str):
        raise ValueError("intent and code_context must be strings")
    if not intent.strip():
        raise ValueError("workload description must not be empty")
    if len(intent) > MAX_INTENT_CHARACTERS or len(code_context) > MAX_CODE_CONTEXT_CHARACTERS:
        raise ValueError("workload context exceeds the supported preview size")
    parsed_size: float | None = None
    if "dataset_size_gb" in values and values["dataset_size_gb"] is not None and values["dataset_size_gb"] != "":
        dataset_size = values["dataset_size_gb"]
        if not isinstance(dataset_size, (int, float, str)) or isinstance(dataset_size, bool):
            raise ValueError("dataset_size_gb must be numeric")
        try:
            parsed_size = float(dataset_size)
        except (TypeError, ValueError):
            raise ValueError("dataset_size_gb must be a finite non-negative number") from None
        if not math.isfinite(parsed_size) or parsed_size < 0:
            raise ValueError("dataset_size_gb must be a finite non-negative number")
    else:
        parsed_size = _extract_dataset_size_gb(intent)
    return RecommendationRequest(
        intent=intent,
        dataset_size_gb=parsed_size,
        code_context=code_context,
    )


class RecommendationPreviewRuntime:
    """Own backend execution and one-time, generation-bound preview records."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        deployment: DeploymentMetadata | None = None,
        catalog: Mapping[str, Any] | None = None,
        backend: Recommender | None = None,
        executor: AsyncRecommendationExecutor | None = None,
    ) -> None:
        selected = os.environ if environ is None else environ
        self.deployment = deployment or validate_deployment_environment(selected)
        self.catalog = dict(catalog) if catalog is not None else load_image_catalog()
        self.images = self.catalog["images"]
        self.policy = PolicyValidator.from_catalog(
            profiles=PROFILES,
            catalog=self.catalog,
        )
        self.backend = backend or create_recommender(environ=selected, catalog=self.catalog)
        maximum = int(
            getattr(
                getattr(self.backend, "config", None),
                "max_concurrent_recommendations",
                4,
            )
        )
        self.executor = executor or AsyncRecommendationExecutor(maximum)
        self.monotonic = monotonic
        self.previews: dict[str, dict[str, Any]] = {}
        self.resource_enricher: Callable[[dict[str, Any], RecommendationRequest], dict[str, Any] | None] | None = None
        self.resource_revalidator: Callable[[dict[str, Any]], None] | None = None
        backend_generation = getattr(self.backend, "generation", {})
        if not isinstance(backend_generation, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in backend_generation.items()
        ):
            raise ValueError("recommender backend generation must contain string versions")
        self.extra_generation: dict[str, str] = dict(backend_generation)

    @property
    def generation(self) -> dict[str, str]:
        return {
            "policy_version": self.policy.policy_version,
            "catalog_version": self.catalog["catalog_version"],
            "package_version": self.deployment.package_version,
            "package_checksum": self.deployment.package_checksum,
            **self.extra_generation,
        }

    def _prune(self) -> None:
        now = self.monotonic()
        for preview_id in [
            key
            for key, item in self.previews.items()
            if now >= item["expires_at"]
        ]:
            self.previews.pop(preview_id, None)
        while len(self.previews) >= PREVIEW_MAX_ENTRIES:
            oldest = min(self.previews, key=lambda key: self.previews[key]["issued_at"])
            self.previews.pop(oldest, None)

    async def issue(self, username: str, values: object) -> dict[str, Any]:
        request = validate_preview_request(values)
        result = await self.executor.recommend(self.backend, request)
        if not isinstance(result, RecommendationResult):
            raise TypeError("recommender executor returned an invalid result")
        recommendation = self.policy.validate(result.recommendation)
        unified = recommendation.to_unified_dict()
        applied_profile = "large" if recommendation.profile == "gpu_or_large" else recommendation.profile
        metadata = result.metadata.to_operational_dict()
        preview_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())
        now = self.monotonic()
        record: dict[str, Any] = {
            "recommendation_preview_id": preview_id,
            "username": username,
            "event_id": event_id,
            "issued_at": now,
            "expires_at": now + PREVIEW_TTL_SECONDS,
            "generation": self.generation,
            "recommendation": unified,
            "applied_profile": applied_profile,
            "applied_image_id": recommendation.image_id,
            "metadata": metadata,
        }
        if p2_requires_manual_override(metadata) or p3_requires_manual_override(metadata):
            record["requires_manual_override"] = True
        if self.resource_enricher is not None:
            resource_decision = self.resource_enricher(record, request)
            if resource_decision is not None:
                record["resource_decision"] = resource_decision
        self._prune()
        self.previews[preview_id] = record
        response = {
            "preview_version": PREVIEW_VERSION,
            "recommendation_preview_id": preview_id,
            "recommendation": unified,
            "applied_profile": applied_profile,
            "image_display_name": self.images[recommendation.image_id]["display_name"],
            "metadata": metadata,
        }
        if "resource_decision" in record:
            response["resource_decision"] = record["resource_decision"]
        if record.get("requires_manual_override"):
            response["requires_manual_override"] = True
            infeasibility = metadata.get("infeasibility_info")
            if isinstance(infeasibility, Mapping):
                record["infeasibility"] = dict(infeasibility)
                response["infeasibility"] = dict(infeasibility)
            else:
                generic_infeasibility = {
                    "infeasible": True,
                    "message": "No approved profile fully satisfies this workload.",
                    "action_guidance": (
                        "Please edit your workload intent or explicitly choose a manual override below."
                    ),
                }
                record["infeasibility"] = generic_infeasibility
                response["infeasibility"] = generic_infeasibility
        return response

    def validate(self, preview_id: object, username: str, *, consume: bool) -> dict[str, Any]:
        if not isinstance(preview_id, str) or not preview_id:
            raise ValueError("a current server-side recommendation preview is required")
        self._prune()
        item = self.previews.get(preview_id)
        if item is None:
            raise ValueError("recommendation preview is unknown, expired, restarted, or already used")
        if item["username"] != username:
            raise ValueError("recommendation preview token is not bound to this user")
        if item["generation"] != self.generation:
            self.previews.pop(preview_id, None)
            raise ValueError("recommendation preview generation is stale")
        if self.resource_revalidator is not None:
            self.resource_revalidator(item)
        if consume:
            self.previews.pop(preview_id, None)
        return item

    def options_from_form(self, spawner: object, formdata: Mapping[str, Any]) -> dict[str, Any]:
        def first(name: str, default: str = "") -> str:
            values = formdata.get(name, [default])
            value = values[0] if isinstance(values, (list, tuple)) else values
            return value.decode("utf-8") if isinstance(value, bytes) else str(value)

        if first("preview_version") != PREVIEW_VERSION:
            raise ValueError("a current recommendation preview is required")
        action = first("decision_action")
        if action not in {"accept", "override"}:
            raise ValueError("confirm the recommendation or submit a manual override")
        preview_id = first("recommendation_preview_id")
        item = self.validate(preview_id, _username(spawner), consume=False)
        if action == "accept" and item.get("requires_manual_override"):
            raise ValueError(
                "the recommender found no feasible catalog candidate; an explicit "
                "manual override is required"
            )
        recommendation = item["recommendation"]
        profile = item["applied_profile"]
        image_id = item["applied_image_id"]
        if action == "override":
            profile = first("override_profile")
            image_id = first("override_image_id")
            if profile not in PROFILE_RESOURCES:
                raise ValueError("manual profile override is not allowlisted")
            if image_id not in self.images:
                raise ValueError("manual image override is not allowlisted")
        confirmation = {
            "decision_action": action,
            "applied_profile": profile,
            "applied_image_id": image_id,
        }
        if item.get("confirmation") not in (None, confirmation):
            raise ValueError("recommendation preview has already been confirmed differently")
        item["confirmation"] = confirmation
        options = {
            "recommendation_preview_id": preview_id,
            "preview_version": PREVIEW_VERSION,
            "decision_action": action,
            "event_id": item["event_id"],
            "recommended_profile": recommendation["profile"],
            "applied_profile": profile,
            "recommended_image_id": recommendation["image_id"],
            "applied_image_id": image_id,
            "recommender_backend": recommendation["backend_name"],
            "recommender_version": recommendation["backend_version"],
            **item["generation"],
        }
        if action == "override":
            options["override_profile"] = profile
            options["override_image_id"] = image_id
        return options

    async def pre_spawn(self, spawner: object) -> None:
        options = dict(getattr(spawner, "user_options", {}) or {})
        item = self.validate(
            options.get("recommendation_preview_id"),
            _username(spawner),
            consume=False,
        )
        action = options.get("decision_action")
        if action not in {"accept", "override"}:
            raise ValueError("invalid recommendation decision action")
        recommendation = item["recommendation"]
        if action == "override":
            profile = options.get("override_profile")
            image_id = options.get("override_image_id")
        else:
            profile = item["applied_profile"]
            image_id = item["applied_image_id"]
        expected_confirmation = {
            "decision_action": action,
            "applied_profile": profile,
            "applied_image_id": image_id,
        }
        if item.get("confirmation") != expected_confirmation:
            raise ValueError("recommendation preview was not confirmed through the spawn form")
        bound_options = {
            "event_id": item["event_id"],
            "recommended_profile": recommendation["profile"],
            "applied_profile": profile,
            "recommended_image_id": recommendation["image_id"],
            "applied_image_id": image_id,
            "recommender_backend": recommendation["backend_name"],
            "recommender_version": recommendation["backend_version"],
            **item["generation"],
        }
        if any(options.get(key) != value for key, value in bound_options.items()):
            raise ValueError("spawn options changed after recommendation confirmation")
        if profile not in PROFILE_RESOURCES or image_id not in self.images:
            raise ValueError("preview selection is no longer allowlisted")

        # Consume only after all binding checks pass. No backend recomputation is
        # performed in this synchronous spawn path.
        self.previews.pop(item["recommendation_preview_id"], None)

        if (
            action == "accept"
            and "resource_decision" in item
            and item["resource_decision"].get("resources")
        ):
            resources = item["resource_decision"]["resources"]
            spawner.cpu_guarantee = resources["cpu_request_millicores"] / 1000
            spawner.cpu_limit = resources["cpu_limit_millicores"] / 1000
            spawner.mem_guarantee = resources["memory_request_mib"] * 2**20
            spawner.mem_limit = resources["memory_limit_mib"] * 2**20
            gpu_count = int(resources.get("gpu_count", 0) or 0)
            gpu_resource = resources.get("gpu_resource")
            spawner.extra_resource_guarantees = (
                {gpu_resource: gpu_count} if gpu_count and gpu_resource else {}
            )
            spawner.extra_resource_limits = dict(spawner.extra_resource_guarantees)
        else:
            resources = PROFILE_RESOURCES[profile]
            spawner.cpu_guarantee = resources["cpu_guarantee"]
            spawner.cpu_limit = resources["cpu_limit"]
            spawner.mem_guarantee = resources["mem_guarantee"]
            spawner.mem_limit = resources["mem_limit"]
            spawner.extra_resource_guarantees = {}
            spawner.extra_resource_limits = {}
        spawner.image = self.images[image_id]["reference"]
        spawner._intent_spawner_preview_item = item

        metadata = item["metadata"]
        annotations = dict(getattr(spawner, "extra_annotations", {}) or {})
        annotations.update(
            {
                "intent-spawner.local/event-id": item["event_id"],
                "intent-spawner.local/backend": recommendation["backend_name"],
                "intent-spawner.local/backend-version": recommendation["backend_version"],
                "intent-spawner.local/profile": profile,
                "intent-spawner.local/image": image_id,
                "intent-spawner.local/fallback-category": metadata.get("fallback_error_category") or "none",
                "intent-spawner.local/attempts": str(metadata.get("attempt_count", 0)),
                "intent-spawner.local/latency-ms": str(round(1000 * float(metadata.get("total_elapsed_seconds", 0)))),
                "intent-spawner.local/policy-version": item["generation"]["policy_version"],
                "intent-spawner.local/catalog-version": item["generation"]["catalog_version"],
                "intent-spawner.local/package-version": item["generation"]["package_version"],
                "intent-spawner.local/package-checksum": item["generation"]["package_checksum"],
            }
        )
        p2_provenance = metadata.get("p2_provenance")
        if isinstance(p2_provenance, Mapping):
            p2_annotation_fields = {
                "pipeline-version": "pipeline_version",
                "intent-schema-version": "structured_intent_schema_version",
                "extractor-version": "extractor_version",
                "dense-index-version": "dense_index_version",
                "sparse-index-version": "sparse_index_version",
                "hybrid-index-version": "hybrid_index_version",
                "candidate-count": "candidate_count",
                "feasible-count": "feasible_candidate_count",
                "final-candidate-id": "final_candidate_id",
                "p2-fallback-category": "fallback_category",
            }
            for annotation_suffix, provenance_key in p2_annotation_fields.items():
                value = p2_provenance.get(provenance_key)
                if value is not None:
                    annotations[f"intent-spawner.local/{annotation_suffix}"] = str(value)
        p3_provenance = metadata.get("p3_provenance")
        if isinstance(p3_provenance, Mapping):
            p3_annotation_fields = {
                "p3-pipeline-version": "pipeline_version",
                "p3-reranker-version": "reranker_version",
                "p3-reranker-model-id": "reranker_model_id",
                "p3-reranker-degraded": "reranker_degraded",
                "p3-final-candidate-id": "final_candidate_id",
                "p3-fallback-category": "fallback_category",
            }
            for annotation_suffix, provenance_key in p3_annotation_fields.items():
                value = p3_provenance.get(provenance_key)
                if value is not None:
                    annotations[f"intent-spawner.local/{annotation_suffix}"] = str(value)
        if "resource_policy_version" in item["generation"]:
            annotations["intent-spawner.local/resource-policy-version"] = item[
                "generation"
            ]["resource_policy_version"]
        if "resource_policy_hash" in item["generation"]:
            annotations["intent-spawner.local/resource-policy-hash"] = item[
                "generation"
            ]["resource_policy_hash"]
        spawner.extra_annotations = annotations
        audit = {
            "event": "recommendation_decision",
            "event_id": item["event_id"],
            "backend": recommendation["backend_name"],
            "backend_version": recommendation["backend_version"],
            "profile": profile,
            "image_id": image_id,
            "fallback_category": metadata.get("fallback_error_category"),
            "attempts": metadata.get("attempt_count", 0),
            "latency_seconds": metadata.get("total_elapsed_seconds", 0),
            **item["generation"],
        }
        if isinstance(p2_provenance, Mapping):
            audit["p2_provenance"] = dict(p2_provenance)
        if isinstance(p3_provenance, Mapping):
            audit["p3_provenance"] = dict(p3_provenance)
        spawner.log.info("recommendation_audit=%s", json.dumps(audit, sort_keys=True))


def _catalog_options(runtime: RecommendationPreviewRuntime) -> str:
    return "".join(
        '<option value="{}">{} — {}</option>'.format(
            safe_escape_truncate(image_id),
            safe_escape_truncate(item["display_name"]),
            safe_escape_truncate(item["description"]),
        )
        for image_id, item in runtime.images.items()
    )


def options_form(runtime: RecommendationPreviewRuntime, endpoint: str) -> str:
    template = r"""
    <div id="recommendation-flow" style="max-width:800px;margin:0 auto;font-family:sans-serif">
      <h2>Select Notebook Workload</h2>
      <p>Describe the planned computation in natural language, preview the server recommendation, then confirm it.</p>
      <input id="decision_action" name="decision_action" type="hidden" value=""/>
      <input id="recommendation_preview_id" name="recommendation_preview_id" type="hidden" value=""/>
      <input name="preview_version" type="hidden" value="__PREVIEW_VERSION__"/>
      <label for="intent"><strong>Describe your workload</strong></label><br/>
      <small style="display:block;margin-top:0.25rem;margin-bottom:0.5rem;color:#555">Describe what you want to do, your framework or libraries, approximate data size, GPU needs, or any other requirements in your own words.</small>
      <textarea id="intent" rows="5" style="width:100%;box-sizing:border-box;padding:0.5rem" placeholder="I want to fine-tune a small PyTorch image classification model using about 8 GB of images. I may need CUDA and pandas for preprocessing."></textarea>
      <div style="margin-top:0.75rem;margin-bottom:0.5rem">
        <button id="preview-recommendation" type="button" class="btn btn-primary">Preview recommendation</button>
      </div>
      <p id="preview-error" hidden role="alert" style="color:#d9534f;margin-top:0.5rem"></p>
      <section id="recommendation-preview" hidden style="margin-top:1rem;padding:1rem;border:1px solid #bbb">
        <div id="infeasibility-warning" hidden role="alert" style="margin-bottom:1rem;padding:0.75rem 1rem;background-color:#fff3cd;border:1px solid #ffeeba;border-left:5px solid #ffc107;color:#856404;border-radius:4px">
          <h4 style="margin:0 0 0.5rem 0;color:#856404;display:flex;align-items:center;gap:0.5rem">
            <span>⚠</span> <span>Workload exceeds available resources</span>
          </h4>
          <div id="infeasibility-req-line" hidden style="margin-bottom:0.25rem"><strong>Your requirement:</strong> <span id="infeasibility-req-text"></span></div>
          <div id="infeasibility-avail-line" hidden style="margin-bottom:0.25rem"><strong>Catalog limits per resource:</strong> <span id="infeasibility-avail-text"></span></div>
          <div id="infeasibility-unmet-line" hidden style="margin-bottom:0.25rem"><strong>Unmet constraints:</strong> <span id="infeasibility-unmet-text"></span></div>
          <p id="infeasibility-message" style="margin:0.5rem 0 0.25rem 0;font-weight:600">No approved profile fully satisfies this workload.</p>
          <p style="margin:0;font-size:0.9rem">The normal recommendation cannot be confirmed. Please edit your workload intent above or explicitly choose an approved profile using the manual override below.</p>
        </div>
        <h3>Recommendation Preview</h3>
        <dl><dt>Resource profile</dt><dd id="preview-profile"></dd>
        <dt>Notebook image</dt><dd id="preview-image"></dd>
        <dt>Backend</dt><dd id="preview-backend"></dd>
        <dt>Explanation</dt><dd><ul id="preview-reasons"></ul></dd></dl>
        <button id="confirm-recommendation" data-recommendation-submit="true" type="submit" class="btn btn-success" disabled>Confirm recommendation</button>
        <button id="manual-override" type="button" class="btn btn-warning">Manual Override</button>
      </section>
      <section id="override-panel" hidden>
        <label for="override_profile">Resource profile</label>
        <select id="override_profile" name="override_profile"><option>small</option><option>medium</option><option>large</option></select>
        <label for="override_image_id">Notebook image</label>
        <select id="override_image_id" name="override_image_id">__IMAGE_OPTIONS__</select>
        <button id="submit-override" data-recommendation-submit="true" type="submit" class="btn btn-warning" disabled>Confirm override</button>
      </section>
    </div>
    <script>
    (() => {
      const root=document.getElementById("recommendation-flow"), form=root.closest("form");
      const intentField=document.getElementById("intent");
      const token=document.getElementById("recommendation_preview_id"), action=document.getElementById("decision_action");
      const panel=document.getElementById("recommendation-preview"), overridePanel=document.getElementById("override-panel");
      const confirm=document.getElementById("confirm-recommendation"), overrideSubmit=document.getElementById("submit-override");
      let fingerprint="";
      const values=()=>({intent:intentField.value});
      const current=()=>JSON.stringify(values());
      const cookie=name=>document.cookie.split(";").map(v=>v.trim()).find(v=>v.startsWith(name+"="))?.split("=").slice(1).join("=")||"";
      function invalidate(){fingerprint="";token.value="";action.value="";panel.hidden=true;overridePanel.hidden=true;confirm.disabled=true;overrideSubmit.disabled=true;document.getElementById("infeasibility-warning").hidden=true;}
      intentField.addEventListener("input",invalidate);
      document.getElementById("preview-recommendation").addEventListener("click",async()=>{
        invalidate(); const error=document.getElementById("preview-error"); error.hidden=true;
        const text=(intentField.value||"").trim();
        if(!text){
          error.textContent="Please enter a workload description before previewing.";
          error.hidden=false;
          return;
        }
        try {
          const response=await fetch(__ENDPOINT__,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-XSRFToken":decodeURIComponent(cookie("_xsrf"))},body:JSON.stringify(values())});
          const payload=await response.json(); if(!response.ok) throw new Error(payload.error||"preview failed");
          const rec=payload.recommendation; token.value=payload.recommendation_preview_id; fingerprint=current();
          document.getElementById("preview-profile").textContent=rec.profile+(rec.profile==="gpu_or_large"?" (applied as large)":"");
          document.getElementById("preview-image").textContent=payload.image_display_name+" ("+rec.image_id+")";
          document.getElementById("preview-backend").textContent=rec.backend_name+" / "+rec.backend_version+(payload.metadata.fallback_used?" (fallback)":"");
          const reasons=[...rec.reasons,...rec.image_reasons]; document.getElementById("preview-reasons").replaceChildren(...reasons.map(reason=>{const li=document.createElement("li");li.textContent=reason;return li;}));
          document.getElementById("override_profile").value=payload.applied_profile; document.getElementById("override_image_id").value=rec.image_id;
          panel.hidden=false; confirm.disabled=Boolean(payload.requires_manual_override); overrideSubmit.disabled=false;
          const warningBox=document.getElementById("infeasibility-warning");
          if(payload.requires_manual_override){
            const inf=payload.infeasibility||{};
            const req=inf.requested_resources||{};
            const reqParts=[];
            if(req.cpu_cores!=null) reqParts.push("CPU ≥ "+req.cpu_cores+" cores");
            if(req.memory_gb!=null) reqParts.push("RAM ≥ "+req.memory_gb+" GB");
            if(req.gpu!=null) reqParts.push("GPU: "+req.gpu);
            const reqLine=document.getElementById("infeasibility-req-line");
            if(reqParts.length>0){
              document.getElementById("infeasibility-req-text").textContent=reqParts.join(" · ");
              reqLine.hidden=false;
            } else {
              reqLine.hidden=true;
            }

            const limits=inf.catalog_limits||{};
            const limitParts=[];
            if(limits.cpu_cores!=null) limitParts.push("CPU up to "+limits.cpu_cores+" cores");
            if(limits.memory_gb!=null) limitParts.push("RAM up to "+limits.memory_gb+" GB");
            if(limits.gpu_count!=null) limitParts.push("GPU up to "+limits.gpu_count);
            const availLine=document.getElementById("infeasibility-avail-line");
            if(limitParts.length>0){
              document.getElementById("infeasibility-avail-text").textContent=limitParts.join(" · ");
              availLine.hidden=false;
            } else {
              availLine.hidden=true;
            }

            const unmet=inf.unmet_constraints||[];
            const extraUnmet=unmet.filter(c=>!c.startsWith("minimum_cpu_cores:")&&!c.startsWith("minimum_memory_gb:")&&c!=="gpu:required");
            const unmetLine=document.getElementById("infeasibility-unmet-line");
            if(extraUnmet.length>0){
              document.getElementById("infeasibility-unmet-text").textContent=extraUnmet.join(", ");
              unmetLine.hidden=false;
            } else {
              unmetLine.hidden=true;
            }

            if(inf.message){
              document.getElementById("infeasibility-message").textContent=inf.message;
            }
            warningBox.hidden=false;
            overridePanel.hidden=false;
          } else {
            warningBox.hidden=true;
          }
        } catch (exc) { error.textContent=exc.message||"Recommendation preview failed. Check the inputs or ask an administrator to inspect safe Hub telemetry."; error.hidden=false; }
      });
      confirm.addEventListener("click",()=>{action.value="accept";});
      document.getElementById("manual-override").addEventListener("click",()=>{overridePanel.hidden=false;});
      overrideSubmit.addEventListener("click",()=>{action.value="override";});
      form.addEventListener("submit",event=>{if(!token.value||fingerprint!==current()||!["accept","override"].includes(action.value)){event.preventDefault();action.value="";window.alert("Preview the current inputs and confirm before creating the pod.");}});
      const hide=()=>form.querySelectorAll('input[type="submit"],button[type="submit"]').forEach(button=>{if(!button.dataset.recommendationSubmit)button.style.display="none";}); hide();
    })();
    </script>
    """
    return (
        template.replace("__PREVIEW_VERSION__", safe_escape_truncate(PREVIEW_VERSION))
        .replace("__IMAGE_OPTIONS__", _catalog_options(runtime))
        .replace("__ENDPOINT__", safe_json_dumps(endpoint))
    )


def install_jupyterhub(c: Any) -> dict[str, Any]:
    """Validate startup, register the async handler, and configure KubeSpawner."""

    from jupyterhub.handlers.base import BaseHandler
    from tornado import web

    runtime = RecommendationPreviewRuntime()
    base_url = str(getattr(c.JupyterHub, "base_url", "/") or "/")
    if not base_url.startswith("/"):
        base_url = "/" + base_url
    if not base_url.endswith("/"):
        base_url += "/"
    browser_endpoint = f"{base_url}hub/recommendation-preview"

    class RecommendationPreviewHandler(BaseHandler):
        @web.authenticated
        async def post(self) -> None:
            try:
                body = json.loads(self.request.body.decode("utf-8")) if self.request.body else {}
                response = await runtime.issue(_username(self), body)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                self.set_status(400)
                self.set_header("Content-Type", "application/json")
                self.finish(json.dumps({"error": str(exc)}))
                return
            except Exception:
                self.log.exception("recommendation preview failed")
                self.set_status(503)
                self.set_header("Content-Type", "application/json")
                self.finish(json.dumps({"error": "recommendation backend unavailable"}))
                return
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps(response))

    c.JupyterHub.extra_handlers.append((r"/recommendation-preview", RecommendationPreviewHandler))

    async def context_options_form(spawner: object) -> str:
        return options_form(runtime, browser_endpoint)

    def context_options_from_form(
        formdata: Mapping[str, Any], *, spawner: object
    ) -> dict[str, Any]:
        # JupyterHub detects the named ``spawner`` parameter and supplies it as
        # a keyword. Keeping formdata first is required by run_options_from_form.
        return runtime.options_from_form(spawner, formdata)

    async def context_pre_spawn_hook(spawner: object) -> None:
        await runtime.pre_spawn(spawner)

    c.KubeSpawner.options_form = context_options_form
    c.KubeSpawner.options_from_form = context_options_from_form
    c.KubeSpawner.pre_spawn_hook = context_pre_spawn_hook
    return {
        "RECOMMENDATION_RUNTIME": runtime,
        "RECOMMENDATION_PREVIEWS": runtime.previews,
        "RECOMMENDATION_POLICY_VERSION": runtime.policy.policy_version,
        "IMAGE_CATALOG": runtime.images,
        "IMAGE_CATALOG_VERSION": runtime.catalog["catalog_version"],
        "PROFILE_RESOURCES": PROFILE_RESOURCES,
        "PREVIEW_VERSION": PREVIEW_VERSION,
        "RecommendationPreviewHandler": RecommendationPreviewHandler,
        "context_options_form": context_options_form,
        "context_options_from_form": context_options_from_form,
        "context_pre_spawn_hook": context_pre_spawn_hook,
        "safe_escape_truncate": safe_escape_truncate,
        "safe_json_dumps": safe_json_dumps,
    }


__all__ = [
    "MAX_CODE_CONTEXT_CHARACTERS",
    "MAX_INTENT_CHARACTERS",
    "PACKAGE_VERSION",
    "PREVIEW_MAX_ENTRIES",
    "PREVIEW_TTL_SECONDS",
    "PREVIEW_VERSION",
    "PROFILE_RESOURCES",
    "RecommendationPreviewRuntime",
    "install_jupyterhub",
    "options_form",
    "safe_escape_truncate",
    "safe_json_dumps",
    "validate_preview_request",
]
