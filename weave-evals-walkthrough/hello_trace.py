"""
hello_trace.py — Part 1 smoke test for the cw-add-users Weave eval setup.

Run it once to confirm auth + project + tracing all work:

    # if your venv is active:
    python weave-evals-walkthrough/hello_trace.py

    # or, with uv (no activation needed):
    uv run python weave-evals-walkthrough/hello_trace.py

Success = the console prints a 🍩 link. Click it to see the
`summarize_request` call in the Traces tab of the cw-add-users-evals project.
"""

import weave

# Creates (or connects to) the project under your default W&B entity.
client = weave.init("cw-add-users-evals")
print("Logging to:", client.entity, "/", client.project)


@weave.op()
def summarize_request(groups: list, users: list) -> dict:
    """Stand-in for real work — just so we see a trace."""
    return {"group_count": len(groups), "user_count": len(users)}


result = summarize_request(["engineering"], ["a@x.com", "b@x.com"])
print("Returned:", result)
print("Done — click the 🍩 link above to view the trace in Weave.")
