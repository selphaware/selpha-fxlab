# Proposed harness changes — for the user to apply

`.claude/` is deny-edited, so the agent cannot install these. That is the rule
working: an agent that can rewrite its own hook and its own permissions has
neither. The agent proposes; you decide.

## What is here

| file | replaces | why |
|---|---|---|
| `gate_hook.py` | `.claude/hooks/gate_hook.py` | routes edits under `research/`, `tests2/`, `experiments/`, `taskcards/` to `verify2/research_gate.py --fast`; Phase 1 behaviour for `fxlab/`, `tests/`, `config/` is unchanged |
| `settings.json` | `.claude/settings.json` | adds `Edit(./research/**)`, `Edit(./tests2/**)`, `Edit(./experiments/**)`, `Edit(./reports/**)`, `Edit(./taskcards/**)` to allow, and `Edit(./verify2/**)` to deny. `verify/` stays denied |

## Applying them

```
cp verify2/proposed/gate_hook.py  .claude/hooks/gate_hook.py
cp verify2/proposed/settings.json .claude/settings.json
```

Then **restart the session**. Claude Code reads hooks and settings at startup,
so neither change takes effect in a session that is already running. Until the
restart, editing `research/` still runs the Phase 1 gate rather than the fast
research subset — which is safe, just less informative.

## Proof it works, without a restart

The hook is a plain script that reads a PostToolUse payload on stdin, so it can
be fired directly. This was run on 2026-08-19 against the file in this
directory:

```
[unwatched      ] reports/  -> exit 0 in  0.3s   (no gate ran)
[phase 1        ] fxlab/    -> exit 0 in 47.2s   (Phase 1 gate)
[research       ] research/ -> exit 0 in 13.4s   (research gate --fast)
[research/breach] research/ -> exit 2 in 48.5s   (blocked, SEAL_DATA_PRESENT)
```

The last line is the one worth having: a sealed-date Parquet was planted under
`data/research/`, the fast gate refused it, and the hook returned 2 with the
named reason on stderr — which is what the agent would see. The planted file
was removed afterwards.

To repeat it:

```python
import json, subprocess
payload = {"tool_name": "Edit",
           "tool_input": {"file_path": r"E:\CODE\selpha-fxlab\research\seal.py"}}
subprocess.run([r"E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe", "-E", "-s",
                r"E:\CODE\selpha-fxlab\verify2\proposed\gate_hook.py"],
               input=json.dumps(payload), text=True,
               cwd=r"E:\CODE\selpha-fxlab")
```

Point it at `.claude/hooks/gate_hook.py` instead once you have copied it.
