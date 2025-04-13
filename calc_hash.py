#!/usr/bin/env python3

def calc_hash(input: str) -> int:
    # Taken from rewwise
    # https://github.com/vswarte/rewwise/blob/127d665ab5393fb7b58f1cade8e13a46f71e3972/analysis/src/fnv.rs#L6
    FNV_BASE = 2166136261
    FNV_PRIME = 16777619
    
    input_lower = input.lower()
    input_bytes = input_lower.encode()
    
    result = FNV_BASE
    for byte in input_bytes:
        result *= FNV_PRIME
        # Ensure it stays within 32-bit range
        result &= 0xFFFFFFFF
        result ^= byte
    
    return result

print(calc_hash("Play_s445205011"))

if __name__ == "__main__":
    import sys
    
    for arg in sys.argv[1:]:
        h = calc_hash(arg)
        print(f"{arg}:\t{h}")
