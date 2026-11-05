"""The valve: a decision machine (the owner, 2026-11-04: "a 'decision' type of machine ... It would
need to collect some information to help make decisions, but it could be a really easy way to add
filters"). In system dynamics a valve regulates a flow; this one decides, per item, which way the
item flows, by asking a model the owner has access to.

It is a program the agents package ships to the Workflows plugin (the entry point
``orchestra_workflows.programs``: ``valve = "orchestra_agents.valve"``), run by its program host as
``<python> -I -m orchestra_agents.valve --declaration <valve.json>`` for each item at a step that
names the program (``who: {"program": <id>}``). It collects exactly the inputs its declaration names
(``inputs.py``), asks the model in one of three API shapes (``apis.py``, over ``http.py``), and
exits with the code the declared label maps to, so the host's ``exit`` map makes the label the
step's outcome; its result line sets the decision (label, score, one-line reason) as a field of the
item. Below ``min_confidence``, with the model down, or with a label outside the declared ones, the
outcome is the declared ``fallback``. A daily cap and a cache by the inputs' fingerprint bound what
it spends (``ledger.py``); every call is logged in the state directory without the token or the
text. Its counts are measured by the plugin's instrument ``agents.valves`` (``metrics.py``), never
by the valve itself (Orchestra's rule 7). The Supervisor declares it (``declare_valve``,
``valve_tools.py``). Orchestra's core runs no AI (its rule 1): this lives here, in a plugin."""
