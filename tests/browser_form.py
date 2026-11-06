from html.parser import HTMLParser


class _Forms(HTMLParser):
    def __init__(self, action):
        super().__init__(convert_charrefs=True)
        self.action, self.inside, self.posted = action, False, {}
        self.textarea, self.select, self.first_option, self.chosen = None, None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.inside = a.get("action") == self.action
        if not self.inside:
            return
        if tag == "input" and "name" in a and a.get("type") != "submit":
            if a.get("type") == "checkbox":
                if "checked" in a:
                    self.posted[a["name"]] = a.get("value", "on")
            else:
                self.posted[a["name"]] = (a.get("value") or "").replace("\n", "")
        elif tag == "textarea":
            self.textarea = [a["name"], ""]
        elif tag == "select":
            self.select, self.first_option, self.chosen = a["name"], None, None
        elif tag == "option" and self.select:
            if self.first_option is None:
                self.first_option = a.get("value", "")
            if "selected" in a:
                self.chosen = a.get("value", "")

    def handle_data(self, data):
        if self.textarea:
            self.textarea[1] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False
        elif tag == "textarea" and self.textarea:
            name, text = self.textarea
            self.posted[name] = text.removeprefix("\n").replace("\n", "\r\n")
            self.textarea = None
        elif tag == "select" and self.select:
            self.posted[self.select] = self.chosen if self.chosen is not None else self.first_option
            self.select = None


def browser_submit(page, action):
    parser = _Forms(action)
    parser.feed(page.replace("\r\n", "\n").replace("\r", "\n"))
    parser.close()
    return parser.posted
