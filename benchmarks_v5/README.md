# Protocol-v5 benchmarks

This directory reserves the versioned benchmark namespace for Protocol-v5.
No development cases, confirmatory cases, labels, or sealed-data accessors are
included in this architecture package.

Future benchmark packages must keep development and final confirmatory splits
separate by workload family, record independent dataset IDs and SHA-256
checksums, and prevent implementation or tuning code from reading sealed
confirmatory labels.
