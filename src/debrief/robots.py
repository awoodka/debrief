"""robots.txt per RFC 9309, which urllib.robotparser doesn't fully implement (no `*` or `$`
wildcards, and first-match instead of longest-match). GitHub's and PyPI's rules depend on both.

- The groups whose user-agent matches our product token apply; otherwise the `*` groups do.
  Groups naming the same agent are merged.
- A rule's path may use `*` (any run of characters) and `$` (end of URL). It's matched against
  the URL's path plus its query.
- The longest matching rule wins. On a tie between allow and disallow, allow wins.
"""
import re
from urllib.parse import unquote, urlsplit


def _compile(pattern):
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    rx = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(rx + ("$" if anchored else ""))


class Robots:
    def __init__(self, text, product):
        self.product = product.lower()
        groups, agents, rules, in_rules = [], [], [], False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if in_rules:                       # a user-agent after rules starts a new group
                    groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if value:                          # an empty Disallow means "allow everything"
                    rules.append((key == "allow", value))
        if agents:
            groups.append((agents, rules))
        mine = [r for a, rs in groups if any(self._matches(x) for x in a) for r in rs]
        star = [r for a, rs in groups if "*" in a for r in rs]
        self.rules = [(allow, len(p), _compile(p)) for allow, p in (mine if mine else star)]

    def _matches(self, agent):
        return agent != "*" and agent in self.product

    def allowed(self, url):
        parts = urlsplit(url)
        target = unquote(parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if target == "/robots.txt":
            return True
        best = None                                 # (length, allow)
        for allow, length, rx in self.rules:
            if rx.match(target) and (best is None or length > best[0] or (length == best[0] and allow)):
                best = (length, allow)
        return True if best is None else best[1]
