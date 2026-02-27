# sf entities

Canonical entity operations.

- Output formats: `-F human` (default), `-F json`, `-F yaml`
- Repo override: `-R /path/to/datarepo`

## Subcommands

- `add <sfid> [key=value ...]`
- `ls`
- `show <sfid>`
- `set <sfid> key=value [key=value ...]`
- `retire <sfid> [--reason <text>]`
- `revision ...` (see `sf entities revision --help`)
- `files ...` (see `sf entities files --help`)
- `build ...` (see below)
- `events ...` (see below)

## Build subcommands (finished goods)

Use dedicated helpers for build-specific fields.

### Set serial number

```sh
sf entities build serial <b_sfid> <serialnumber>
# example
sf entities build serial b_2024_0001 SN123
```

- Updates the canonical entity field `serialnumber`.
- Automatically commits the change to Git with required metadata tokens.

### Set datetime (ISO 8601)

```sh
sf entities build datetime <b_sfid> <iso8601>
# examples
sf entities build datetime b_2024_0001 2024-06-01T12:00:00Z
sf entities build datetime b_2024_0001 2024-06-01T12:00:00+00:00
```

- Validates ISO 8601 format. Accepts trailing `Z` and offsets like `+00:00`.
- Updates the canonical entity field `datetime`.
- Automatically commits the change to Git with required metadata tokens.

## Events subcommands (build entities)

Events are supported only for build SFIDs (`b_*`).

### List events

```sh
sf entities events ls <b_sfid>
```

### Append event

```sh
sf entities events append <b_sfid> [key=value ...] [--tags a,b] [--file <files/path>]... [--upload <local/path>]...
# examples
sf entities events append b_2024_0001 message="Bench fail" --tags repair,task_open
sf entities events append b_2024_0001 message="With uploads" --upload ./evidence/log.txt --upload ./evidence/scope.png
```

### Update event

```sh
sf entities events update <b_sfid> <event_id> [key=value ...] [--tags a,b]
```

### Replace tags

```sh
sf entities events tags <b_sfid> <event_id> --tags a,b
```

### Link an existing file path

```sh
sf entities events link-file <b_sfid> <event_id> <files/path>
```

Notes:
- Stored in `entities/<b_sfid>/events.jsonl`.

## Notes

- You can still use `sf entities set` for arbitrary fields, e.g.:

```sh
sf entities set b_2024_0001 serialnumber=SN123 datetime=2024-06-01T12:00:00Z
```

- Commit behavior: All mutating operations auto-commit with clear messages containing `::sfid::<SFID>` tokens.
