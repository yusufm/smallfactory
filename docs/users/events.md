# Build Events

Use build events to track test results, notes, tasks, and attachments per build.

Scope:
- Events are available for build entities (`b_*`) only.
- Events are stored in `entities/<b_sfid>/events.jsonl`.

## CLI quick examples

```sh
# List events
sf entities events ls b_2024_0001

# Add event
sf entities events append b_2024_0001 --message "Bench fail" --tags repair,task_open

# Add event and upload files
sf entities events append b_2024_0001 --message "Captured evidence" \
  --upload ./captures/scope.png \
  --upload ./captures/log.txt
```

## Notes

- Use `tags` for flexible categorization.
- Event fields are fixed: `id`, `ts`, `tags`, `message`, `files`.
