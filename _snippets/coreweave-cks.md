<!--
  CKS-specific (CoreWeave Kubernetes Service) atomic procedures.
  See _snippets/coreweave-platform.md for the snippet/tagged-region
  convention.

  fetch-pinned-ref-arch is the canonical way every skill obtains the
  CoreWeave reference architecture. It lives here rather than in each
  body.md for one reason: the pinned commit SHA below is the repo's
  single source of truth. Three skills fetch that repo and run its
  Terraform and Helm charts under the customer's own credentials, so a
  copy that drifted to a different SHA would be a correctness *and* a
  security bug. Update the pin in one place here;
  `python build.py` propagates it to every rendered copy.
-->

<!-- snippet:fetch-pinned-ref-arch -->
{{ HEADING }}

The fetch is **pinned to the reference-architecture commit this skill was
tested against**. Never clone or pull the live default branch:
{{ PIN_RISK }}
Update the SHA only as a deliberate skill change, and review the upstream diff
(`git log <old-sha>..<new-sha>`) — not just the changed pin line — before you do.

```bash
CW_REF_ARCH_SHA=94c2d5f944c35aa44e7c2bc9decb5caacc911f64
CW_REF_ARCH_DIR=/tmp/claude/cw-ref-arch

mkdir -p "$CW_REF_ARCH_DIR"
git init -q "$CW_REF_ARCH_DIR"
if git -C "$CW_REF_ARCH_DIR" fetch -q --depth 1 \
     https://github.com/coreweave/reference-architecture.git "$CW_REF_ARCH_SHA"
then
  git -C "$CW_REF_ARCH_DIR" checkout -qf "$CW_REF_ARCH_SHA" \
    && [ "$(git -C "$CW_REF_ARCH_DIR" rev-parse HEAD)" = "$CW_REF_ARCH_SHA" ] \
    && echo "PINNED OK $CW_REF_ARCH_SHA"
else
  curl -fsSL -o "$CW_REF_ARCH_DIR.tar.gz" \
      "https://github.com/coreweave/reference-architecture/archive/${CW_REF_ARCH_SHA}.tar.gz" \
    && tar xzf "$CW_REF_ARCH_DIR.tar.gz" -C "$CW_REF_ARCH_DIR" --strip-components=1 \
    && echo "PINNED OK $CW_REF_ARCH_SHA (tarball)"
fi
```

**The block must print `PINNED OK <sha>`, and you must confirm it did before
using anything in that directory.** The check is not decoration: if the pinned
commit cannot be fetched, a stale tree from an earlier run is still sitting in
`$CW_REF_ARCH_DIR`, and every later step would run against unreviewed upstream
code while looking like it succeeded. On anything other than `PINNED OK`, stop
and tell the customer — do not fall back to an unpinned fetch.

The tarball branch is not decoration either. Many developers carry a global
`url.git@github.com:.insteadOf https://github.com/` rewrite, which silently turns
that HTTPS fetch into SSH and fails wherever SSH is unavailable. The error is
`Could not read from remote repository`, which reads like a permissions problem
and is not one. Confirm with `git config --get-regexp 'url\..*insteadOf'`. The
tarball needs neither git credentials nor SSH, it is pinned to the same commit
by the SHA in its URL, and `--strip-components=1` works on both GNU tar and the
BSD tar shipped with macOS.

If `$CW_REF_ARCH_DIR` already exists from a previous run, do **not** `git pull`
and do **not** delete it. Re-run the block above unchanged: it re-pins the
checkout to `$CW_REF_ARCH_SHA` while leaving untracked files — `terraform.tfvars`,
`.terraform/`, and Terraform state — in place. A copy left by the tarball branch
has no `.git`; the block converts it into a pinned git checkout the same way.
<!-- /snippet:fetch-pinned-ref-arch -->
