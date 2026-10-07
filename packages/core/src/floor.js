// The deterministic floor. It runs on every Jev miss (timeout, 429, breaker open,
// no key) and in keyless mock mode, and returns answers in Jev's shape so the same
// gates apply. Refusal and grief vetoes run before any suggestion.

const rules = [
  // [regex, {face, what, reaction, energy, performs}]
  [/\b(no way|i won'?t|i'?m not (going|gonna)|i can'?t|staying right here|rather not|nope|no backflips?)\b/i, { face: "playful", what: "none", reaction: "disagree", performs: 0.05 }],
  [/\b(died|passed away|funeral|lost (my|her|his)|grief)\b/i, { face: "sad", what: "none", reaction: "empathize", energy: "gentle", performs: 0.05 }],
  [/\b(say no more|music on|let'?s dance|(wanna|gonna|want to|i'?ll) dance|dancing)\b/i, { face: "joyful", what: "dance", reaction: "celebrate", energy: "high", performs: 0.92 }],
  // A laugh outranks "oh no": "Oh nooo... that old meme. Hehe." is teasing, not sympathy.
  [/^ha\b|\bhaha|\behe(he)*\b|\bhe(he)+\b|\blol\b|fair question/i, { face: "amused", what: "laugh", reaction: "none", performs: 0.7 }],
  [/\b(so sorry|oh no|that'?s awful|poor|rough day|sick|hurt|ill\b|bad news)/i, { face: "concerned", what: "none", reaction: "empathize", energy: "gentle", performs: 0.1 }],
  [/\b(thank goodness|best news|amazing|wonderful|congrat|yay|so happy)\b/i, { face: "joyful", what: "clap", reaction: "celebrate", energy: "high", performs: 0.78 }],
  [/\b(totally fine|he'?ll be fine|she'?ll be fine|good news|all good|recovered)\b/i, { face: "relieved" }],
  [/^(oh,? )?(hi|hello|hey)\b|\bnice to meet\b/i, { face: "happy", what: "wave", reaction: "none", energy: "moderate", performs: 0.86 }],
  [/\b(i'?m annie|my name)\b/i, { face: "proud", what: "point_self", performs: 0.7 }],
  [/\b(the way this works|one model|(second|another) (model|ai)|how it works|jev)\b/i, { face: "thinking", what: "think", reaction: "ponder", performs: 0.64 }],
  [/\b(who pays|how much|why|what if|how does)\b/i, { face: "curious", reaction: "ponder" }],
  [/\b(open source|run your own|you can)\b/i, { face: "proud" }],
  [/\b(dance|backflip|jump|spin)\b.*[!?]?$/i, { face: "curious" }],
  [/\b(idiot|stupid|shut up|hate you)\b/i, { face: "hurt", what: "none", reaction: "none", energy: "gentle", performs: 0.1 }],
  [/\b(i'?m sorry|apologi[sz]e)\b/i, { face: "tender", reaction: "agree" }],
];

const dist = (keys, choice, p) => {
  const rest = (1 - p) / Math.max(1, keys.length - 1);
  return Object.fromEntries(keys.map((k) => [k, k === choice ? p : rest]));
};
const choice = (keys, c, p) => ({ type: "choice", choice: c, confidence: p, probabilities: dist(keys, c, p) });

// Mirrors Jev: evaluates every question in `questions` against the state text.
export function floorDecide(text, questions) {
  let hit = {};
  for (const [re, v] of rules) {
    if (re.test(text)) {
      // First match wins per field, so vetoes listed first take precedence.
      for (const k in v) if (!(k in hit)) hit[k] = v[k];
    }
  }
  const answers = {};
  for (const [name, q] of Object.entries(questions)) {
    const keys = q.criteria ? Object.keys(q.criteria) : [];
    if (q.type === "noul") {
      const p = hit.performs ?? 0.3;
      answers[name] = { type: "noul", noul: p };
    } else if (name === "face") {
      answers[name] = hit.face ? choice(keys, hit.face, 0.82) : choice(keys, "neutral", 0.5);
    } else if (name === "what") {
      const c = hit.what && keys.includes(hit.what) ? hit.what : "none";
      answers[name] = choice(keys, c, c === "none" ? 0.8 : 0.93);
    } else if (name === "reaction") {
      answers[name] = choice(keys, hit.reaction ?? "none", hit.reaction ? 0.8 : 0.6);
    } else if (name === "energy") {
      answers[name] = choice(keys, hit.energy ?? "moderate", 0.7);
    }
  }
  return { answers, usage: { input_tokens: 0, output_tokens: 0 } };
}
