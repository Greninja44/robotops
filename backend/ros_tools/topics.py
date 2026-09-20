"""Topic-level diagnostic tools (read-only)."""
from __future__ import annotations

from .common import clamp, manifest, ros_name, suggest, tool

IGNORED = {"/rosout", "/parameter_events"}


@tool("list_topics")
def list_topics(client, r):
    topics = client.topics()
    expected = manifest()["topics"]
    table = {}
    for name, types in sorted(topics.items()):
        if name in IGNORED:
            continue
        pubs, subs = client.endpoints(name)
        if not pubs and not subs:
            continue  # only our own endpoints -> not really part of the robot graph
        table[name] = {"type": types[0] if types else None, "publishers": len(pubs), "subscribers": len(subs)}
    r.data["topics"] = table
    for name in expected:
        if name not in table:
            r.anomaly(f"Expected topic {name} does not exist in the ROS graph (no publishers or subscribers)", name)
        elif table[name]["publishers"] == 0:
            r.anomaly(f"Expected topic {name} has no publishers", name)
    for name, t in table.items():
        if name not in expected and name not in ("/diagnostics", "/tf", "/tf_static"):
            r.anomaly(f"Topic {name} is not in the robot manifest ({t['publishers']} publishers, "
                      f"{t['subscribers']} subscribers)", name)
    r.normal(f"{len(table)} active topics: {', '.join(table)}")


def _inspect(client, r, topic):
    topic = ros_name(topic, "topic")
    r.args["topic"] = topic
    topics = client.topics()
    exp = manifest()["topics"].get(topic)
    if topic not in topics:
        r.data.update(exists=False, similar=suggest(topic, list(topics)))
        if exp:
            r.anomaly(f"Expected topic {topic} does not exist in the ROS graph", topic, *exp["publishers"])
        else:
            r.normal(f"Topic {topic} does not exist; similar topics: {r.data['similar']}", topic)
        return None, None, exp
    pubs, subs = client.endpoints(topic)
    r.data.update(exists=True, type=topics[topic][0], publisher_count=len(pubs), subscriber_count=len(subs),
                  publishers=[p["node"] for p in pubs], subscribers=[s["node"] for s in subs])
    return pubs, subs, exp


def _endpoint_findings(r, topic, pubs, subs, exp, which=("publishers", "subscribers")):
    actual = {"publishers": [p["node"] for p in pubs], "subscribers": [s["node"] for s in subs]}
    for kind in which:
        nodes = actual[kind]
        r.normal(f"{topic} has {len(nodes)} {kind}" + (f": {', '.join(nodes)}" if nodes else ""), topic, *nodes)
        if exp:
            for n in exp[kind]:
                if n not in nodes:
                    r.anomaly(f"{topic}: expected {kind[:-1]} {n} is missing", topic, n)
    if exp is None and "subscribers" in which and "publishers" in which:
        if subs and not pubs:
            r.anomaly(f"{topic} has subscribers ({', '.join(actual['subscribers'])}) but no publishers and is not "
                      f"in the robot manifest", topic, *actual["subscribers"])


@tool("inspect_topic")
def inspect_topic(client, r, topic: str):
    pubs, subs, exp = _inspect(client, r, topic)
    if pubs is None:
        return
    topic = r.args["topic"]
    if exp and exp["type"] != r.data["type"]:
        r.anomaly(f"{topic} type is {r.data['type']}, manifest expects {exp['type']}", topic)
    _endpoint_findings(r, topic, pubs, subs, exp)
    qos = {(e["reliability"], e["durability"]) for e in pubs + subs}
    r.data["qos"] = [f"{a}/{b}" for a, b in qos]


@tool("get_publishers")
def get_publishers(client, r, topic: str):
    pubs, subs, exp = _inspect(client, r, topic)
    if pubs is not None:
        _endpoint_findings(r, r.args["topic"], pubs, subs, exp, which=("publishers",))


@tool("get_subscribers")
def get_subscribers(client, r, topic: str):
    pubs, subs, exp = _inspect(client, r, topic)
    if pubs is not None:
        _endpoint_findings(r, r.args["topic"], pubs, subs, exp, which=("subscribers",))


@tool("measure_topic_rate")
def measure_topic_rate(client, r, topic: str, duration: float = 3.0):
    topic = ros_name(topic, "topic")
    duration = clamp(duration, 1.0, 4.0, 2.0)
    r.args.update(topic=topic, duration=duration)
    exp = manifest()["topics"].get(topic)
    s = client.sample_topic(topic, duration)
    if not s["exists"]:
        r.data.update(exists=False, rate_hz=0.0, messages=0)
        r.anomaly(f"{topic} does not exist - nothing to measure", topic, *(exp["publishers"] if exp else []))
        return
    times = s["times"]
    rate = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 2 else len(times) / duration
    r.data.update(exists=True, messages=len(times), duration_s=duration, rate_hz=round(rate, 2),
                  expected_min_hz=exp["min_rate_hz"] if exp else None)
    pubs = exp["publishers"] if exp else []
    if not times:
        r.anomaly(f"{topic}: 0 messages received in a {duration:.0f} s window (not publishing)", topic, *pubs)
    elif exp and rate < exp["min_rate_hz"]:
        r.anomaly(f"{topic}: {rate:.1f} Hz is below the expected minimum {exp['min_rate_hz']} Hz", topic, *pubs)
    else:
        r.normal(f"{topic}: publishing at {rate:.1f} Hz - healthy (measured over a {duration:.0f} s window)", topic, *pubs)
