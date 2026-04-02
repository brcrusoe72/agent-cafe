#!/usr/bin/env python3
"""Fix nested transaction bug in presence.py"""
with open('/app/layers/presence.py', 'r') as f:
    lines = f.readlines()

new_lines = []
skip_next = 0
for i, line in enumerate(lines):
    if skip_next > 0:
        skip_next -= 1
        continue
    if 'BEGIN IMMEDIATE acquires a write lock' in line:
        # Skip this comment line and the next comment + the execute line
        new_lines.append('            # Transaction managed by caller (no explicit BEGIN)\n')
        skip_next = 2  # skip next 2 lines (comment + execute)
        continue
    new_lines.append(line)

with open('/app/layers/presence.py', 'w') as f:
    f.writelines(new_lines)

print(f'Patched: {len(lines)} -> {len(new_lines)} lines')
