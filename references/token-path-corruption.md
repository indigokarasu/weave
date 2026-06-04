# TOKEN_PATH Corruption Diagnosis & Fix

## Symptoms

- `google_sync.py` or `contact_snapshots.py` fails with `FileNotFoundError` for a path that looks like `/root/...json` or `/root/...entials/google-workspace-user.json`
- `TOKEN_PATH` line in the script is shorter than ~80 bytes (the correct line is 80 bytes for owner's path)
- Script appears to run but fetches 0 or 1 contacts (wrong account — token points to a different user)
- Hexdump of the script shows truncated or garbled path bytes

## Corruption Vectors

1. **sed/asterisk replacement**: Using `sed` with patterns containing `*` or special chars can replace too broadly
2. **read_file truncation written back**: The `read_file` tool truncates long paths in its output (e.g., `/root/...json`). If this truncated output is written back to a file, the path becomes permanently corrupted.
3. **Python string quoting in heredoc**: Writing Python code with nested quotes via `python3 -c "..."` causes shell quoting issues. Single quotes inside single-quoted strings break the command.
4. **in-place sed without temp space**: `sed -i` fails with "No space left on device" even when disk has space, because sed needs tmp space.

## Correct TOKEN_PATH Values

Both scripts should point to the same token file:

```python
TOKEN_PATH='/root/...json'
```

- `google_sync.py`: line ~27
- `contact_snapshots.py`: line ~45
- Byte length: 80 bytes (including `TOKEN_PATH=*** and closing `'`)

## Diagnostic Commands

```bash
# Check the actual bytes (not affected by tool output masking)
hexdump -C /path/to/script.py | grep -A3 "TOKEN_PATH"

# Check byte count of the TOKEN_PATH line
python3 -c "
with open('/path/to/script.py', 'rb') as f:
    c = f.read()
idx = c.find(b'TOKEN_PATH')
end = c.find(b'\n', idx)
line = c[idx:end]
print(f'Length: {len(line)} bytes')
print(f'Has owner.operator: {b\"owner.operator\" in line}')
print(f'Has gmail.com.json: {b\"gmail.com.json\" in line}')
"

# Verify AST parses
python3 -c "import ast; ast.parse(open('/path/to/script.py').read()); print('OK')"

# Verify token file is non-empty
ls -la /root/.google_workspace_mcp/credentials/google-workspace-user.json
python3 -c "import json; td=json.load(open('/root/.google_workspace_mcp/credentials/google-workspace-user.json')); print(f'Token file OK, scopes: {len(td.get(\"scopes\", []))} scopes')"
```

## Fix Procedure

1. **Stop the LadybugDB bridge**: `systemctl stop ladybug-bridge-weave.service`
2. **Use `patch` tool** (NOT sed) to replace the corrupted line:
   - `old_string`: the corrupted TOKEN_PATH line exactly as shown by `read_file`
   - `new_string`: `TOKEN_PATH='/root/...json'`
3. **Verify with hexdump** — the `patch` tool's diff output may also mask the path. Always verify with `hexdump -C` to confirm the full path is on disk.
4. **Verify AST**: `python3 -c "import ast; ast.parse(open('script.py').read()); print('OK')"`
5. **Restart bridge**: `systemctl start ladybug-bridge-weave.service`

## Important Notes

- The `terminal` and `read_file` tools may mask long paths with `***` or `...` in their output. This is a **display artifact only**. The actual file content on disk may be correct even if the output looks truncated.
- Always use `hexdump -C` or byte-level Python inspection to verify file contents, not tool output.
- If BOTH `google_sync.py` AND `contact_snapshots.py` have corrupted TOKEN_PATH, fix both in the same session.
- Do NOT use `python3 -c "..."` with nested single quotes to write Python files containing single quotes. Use `write_file` to create a `.py` file instead, then run it.
