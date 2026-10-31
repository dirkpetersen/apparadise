import { h } from "./dom.js";
import { icon } from "./icons.js";
import { setVote } from "./api.js";
import { toast } from "./components/toast.js";

// An upvote toggle: <button aria-pressed> with a chevron and the count.
// Signed out, it asks app.js to start sign-in (and come back here afterwards).
export function voteButton({ subject, key, votes = 0, voted = false, me, label }) {
  let state = { votes, voted };
  const btn = h("button", { class: "vote", type: "button" });
  const paint = () => {
    btn.setAttribute("aria-pressed", String(state.voted));
    btn.setAttribute("aria-label", `${state.voted ? "Remove your upvote for" : "Upvote"} ${label} (${state.votes} vote${state.votes === 1 ? "" : "s"})`);
    btn.title = me ? (state.voted ? "Remove your upvote" : "Upvote") : "Sign in to upvote";
    btn.replaceChildren(icon("up", "icon vote-icon"), h("span", { class: "vote-n", text: String(state.votes) }));
  };
  btn.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!me) { window.dispatchEvent(new CustomEvent("apparadise:signin")); return; }
    const want = !state.voted;
    const before = state;
    state = { votes: state.votes + (want ? 1 : -1), voted: want }; // optimistic
    paint();
    btn.disabled = true;
    try {
      state = await setVote(subject, key, want);
    } catch (err) {
      state = before;
      if (err.status !== 401) toast.error(err.message);
    } finally {
      btn.disabled = false;
      paint();
    }
  });
  paint();
  return btn;
}
