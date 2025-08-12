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

## Notes

- You can still use `sf entities set` for arbitrary fields, e.g.:

```sh
sf entities set b_2024_0001 serialnumber=SN123 datetime=2024-06-01T12:00:00Z
```

- Commit behavior: All mutating operations auto-commit with clear messages containing `::sfid::<SFID>` tokens.
