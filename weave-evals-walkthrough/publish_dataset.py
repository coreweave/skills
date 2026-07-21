"""
publish_dataset.py — Part 2 of the cw-add-users Weave eval walkthrough.

Publishes a one-row Weave Dataset describing a single captured add-users run.
The row holds three things the scorers (Parts 4-5) will need:
  - requested  : the ground-truth intent (groups + users + persona)
  - advice     : what the skill recommended (roles + step order)
  - final_state: the observed end state in CoreWeave (the answer key)

Run (with the weave-evals venv active):
    python weave-evals-walkthrough/publish_dataset.py

Success = it prints a "Published: weave:///..." ref. Open your project's
Datasets tab in the Weave UI to inspect the row.
"""

import weave

# One concrete, correct run: three engineers added the right way.
# Synthetic emails — nothing real.
ROWS = [
    {
        "example_id": "add-users-001",
        "prompt": (
            "Add three engineers — alice@acme.com, bob@acme.com, "
            "carol@acme.com. They manage K8s clusters and object storage "
            "but should not be admins."
        ),

        # ── ground truth: what was asked for ──────────────────────────
        "requested": {
            "groups": [{"name": "engineering"}],
            "users": [
                {"email": "alice@acme.com", "group": "engineering"},
                {"email": "bob@acme.com",   "group": "engineering"},
                {"email": "carol@acme.com", "group": "engineering"},
            ],
            "persona": "engineer",  # K8s + storage, non-admin
        },

        # ── what the skill advised / did ──────────────────────────────
        "advice": {
            "recommended_roles": [
                "CKS Admin", "Object Storage Admin",
                "Access Token Admin", "Observability Viewer",
            ],
            "steps_in_order": [
                "create_group", "create_policy_with_roles", "invite_users",
            ],
        },

        # ── observed end state in CoreWeave (the answer key) ──────────
        # NOTE: freshly-invited users are correctly "pending" until they
        # accept; that is the right final status, not "active".
        "final_state": {
            "groups": [
                {
                    "name": "engineering",
                    "policy": "engineering-access",
                    "roles": [
                        "CKS Admin", "Object Storage Admin",
                        "Access Token Admin", "Observability Viewer",
                    ],
                },
            ],
            "users": [
                {"email": "alice@acme.com", "status": "pending", "groups": ["engineering"]},
                {"email": "bob@acme.com",   "status": "pending", "groups": ["engineering"]},
                {"email": "carol@acme.com", "status": "pending", "groups": ["engineering"]},
            ],
        },

        "metadata": {
            "model": "claude-sonnet-4.6",
            "plugin_version": "0.1.0",
            "channel": "browser-automation",
        },
    },
]


def main() -> None:
    weave.init("cw-add-users-evals")
    dataset = weave.Dataset(name="cw_add_users_examples", rows=ROWS)
    ref = weave.publish(dataset)
    print("Published:", ref.uri())
    print(f"Rows: {len(ROWS)} · open the Datasets tab in the Weave UI to inspect.")


if __name__ == "__main__":
    main()
