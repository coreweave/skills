# s5cmd quick reference

CoreWeave recommends the [CoreWeave fork of s5cmd](https://github.com/coreweave/s5cmd) for high-performance bulk transfers. s5cmd parallelizes operations automatically, making it significantly faster than `aws s3` for large datasets.

---

## Installation

| Platform | Command |
|----------|---------|
| **macOS (Homebrew)** | `brew install peak/tap/s5cmd` |
| **Go** | `go install github.com/peak/s5cmd/v2@master` |
| **Linux** | Download binary from https://github.com/coreweave/s5cmd/releases |
| **Docker** | `docker pull peakcom/s5cmd` |

## Authentication

s5cmd reads credentials from the standard AWS SDK chain. For inline usage without config files:

```bash
AWS_ACCESS_KEY_ID=<KEY> AWS_SECRET_ACCESS_KEY=<SECRET> s5cmd --endpoint-url <ENDPOINT> <command>
```

## Endpoint flag

Always pass `--endpoint-url` **before** the subcommand:

```bash
s5cmd --endpoint-url http://cwlota.com cp ...    # correct
s5cmd cp --endpoint-url http://cwlota.com ...     # WRONG — flag not recognized after subcommand
```

## Core commands

| Operation | Command |
|-----------|---------|
| **List buckets** | `s5cmd --endpoint-url EP ls` |
| **List objects** | `s5cmd --endpoint-url EP ls s3://bucket/` |
| **List recursive** | `s5cmd --endpoint-url EP ls 's3://bucket/*'` |
| **Copy file up** | `s5cmd --endpoint-url EP cp file s3://bucket/key` |
| **Copy file down** | `s5cmd --endpoint-url EP cp s3://bucket/key file` |
| **Copy directory up** | `s5cmd --endpoint-url EP cp 'dir/*' s3://bucket/prefix/` |
| **Copy directory down** | `s5cmd --endpoint-url EP cp 's3://bucket/prefix/*' dir/` |
| **Sync up** | `s5cmd --endpoint-url EP sync dir/ s3://bucket/prefix/` |
| **Sync down** | `s5cmd --endpoint-url EP sync 's3://bucket/prefix/*' dir/` |
| **Delete object** | `s5cmd --endpoint-url EP rm s3://bucket/key` |
| **Delete prefix** | `s5cmd --endpoint-url EP rm 's3://bucket/prefix/*'` |
| **Pipe stdin** | `cat file \| s5cmd --endpoint-url EP pipe s3://bucket/key` |
| **Batch from file** | `s5cmd --endpoint-url EP run commands.txt` |

## Tuning

| Flag | Default | Description |
|------|---------|-------------|
| `--numworkers` | 256 | Number of parallel workers. Increase for many small files, decrease if hitting rate limits. |
| `--dry-run` | off | Show what would be transferred without transferring. |
| `--json` | off | Output in JSON format (useful for scripting). |
| `--stat` | off | Show transfer statistics at the end. |

## Wildcard patterns

s5cmd uses shell-style globs on the S3 side. **Always quote patterns** to prevent local shell expansion:

```bash
s5cmd --endpoint-url EP cp 's3://bucket/data/*.parquet' ./local/
s5cmd --endpoint-url EP rm 's3://bucket/tmp/*'
```

Patterns only match within a single prefix level by default. Use `**` for recursive matching if supported by your s5cmd version.

## Differences from aws s3

| Behavior | aws s3 | s5cmd |
|----------|--------|-------|
| **Parallelism** | Single-threaded by default; tune with `--cli-auto-prompt` | Parallel by default (256 workers) |
| **Recursive copy** | Requires `--recursive` flag | Use wildcard patterns (`'dir/*'`) |
| **Endpoint** | `--endpoint-url` after subcommand | `--endpoint-url` before subcommand |
| **Sync** | Built-in `sync` subcommand | Built-in `sync` subcommand |
| **Config files** | `~/.aws/config` + `~/.aws/credentials` | Uses AWS SDK chain (same files or env vars) |
