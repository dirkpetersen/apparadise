import { h } from "./dom.js";

// Minimal shell highlighting for copy-paste commands: the command word, --flags,
// $VARS and # comments get spans; everything else stays a text node.
const TOKEN = /(#.*$)|(--?[A-Za-z][\w-]*(?:=)?)|(\$\{?[A-Za-z_]\w*\}?)|(\S+)|(\s+)/gm;

export function highlightShell(text) {
  const frag = document.createDocumentFragment();
  for (const line of text.split("\n")) {
    if (frag.childNodes.length) frag.append("\n");
    let first = true;
    for (const m of line.matchAll(TOKEN)) {
      const [tok, comment, flag, variable, word] = m;
      if (comment) frag.append(h("span", { class: "syn-comment", text: comment }));
      else if (flag) frag.append(h("span", { class: "syn-key", text: flag }));
      else if (variable) frag.append(h("span", { class: "syn-value", text: variable }));
      else if (word && first) { frag.append(h("span", { class: "syn-section", text: word })); first = false; }
      else frag.append(tok);
    }
  }
  return frag;
}
