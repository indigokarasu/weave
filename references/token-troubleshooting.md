# Token Troubleshooting

Overview and routing page for Google OAuth token issues.

## Quick Start

1. Run the [pre-flight check](google-token-quick-check.md) first
2. If issues found, follow the [full diagnostic workflow](google-token-diagnostics.md)

## Common Issues

| Symptom | Likely Cause | See |
|---|---|---|
| HTTP 401 on People API | Expired/missing scopes | [Diagnostics](google-token-diagnostics.md) |
| HTTP 400 `invalid_grant` | Dead refresh token | [Diagnostics](google-token-diagnostics.md) |
| Pushed ~118, Failed ~463 (all 401s) | Silent refresh failure mid-run | [Diagnostics](google-token-diagnostics.md) |
| `Cannot find property notes` | Outdated sync script | SKILL.md "Pitfall: Sync script notes property references" |
| Script `IndentationError` | Corrupted TOKEN_PATH line | [Diagnostics](google-token-diagnostics.md) |
