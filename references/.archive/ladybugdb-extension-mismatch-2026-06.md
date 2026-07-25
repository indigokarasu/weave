# LadybugDB Extension Version Mismatch

## Symptom

Running `google_sync.py` (or any script that opens the Weave DB directly via `real_ladybug.Database`) fails with:

```
RuntimeError: IO exception: Failed to load library:
  .../extension/0.15.0/linux_amd64/vector/libvector.lbug_extension
which is needed by extension: vector.
Error: ...libvector.lbug_extension: cannot open shared object file: No such file or directory.
```

The DB metadata references extension version `0.15.0` but only `0.17.0` is installed.

## Diagnosis

```bash
find /root -name "libvector*" 2>/dev/null
```

## Fix

Symlink the installed version to the expected path:

```bash
# For indigo profile:
mkdir -p <hermes-home>/profiles/indigo/home/.lbdb/extension/0.15.0/linux_amd64/vector
ln -sf <hermes-home>/profiles/indigo/home/.lbdb/extension/0.17.0/linux_amd64/vector/libvector.lbug_extension \
      <hermes-home>/profiles/indigo/home/.lbdb/extension/0.15.0/linux_amd64/vector/libvector.lbug_extension

# For default profile:
mkdir -p /root/.lbdb/extension/0.15.0/linux_amd64/vector
ln -sf /root/.lbdb/extension/0.17.0/linux_amd64/vector/libvector.lbug_extension \
      /root/.lbdb/extension/0.15.0/linux_amd64/vector/libvector.lbug_extension
```

## Context

- Discovered June 3, 2026 during Google Contacts sync test
- The DB was created with extension version 0.15.0; the VPS has 0.17.0 installed
- LadybugDB extension directories: `<lbdb_root>/extension/<version>/linux_amd64/<name>/`
- The `vector` extension is required for vector similarity search features
