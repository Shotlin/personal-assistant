// Tiny yes/no and one-word decisions made by meaning (Sarvam chat, no thinking, ~0.3 s), for the rare moments
// where word lists fail: "did he ask me to hang up?", "is this a list of tasks or a question?".
// The transcript can be odd (a wrong language, garbled words), so the prompts say so.
export async function decide(apiKey: string, system: string, user: string, maxTokens = 6): Promise<string> {
  const res = await fetch("https://api.sarvam.ai/v1/chat/completions", {
    method: "POST",
    headers: { "api-subscription-key": apiKey, "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "sarvam-105b-conversations",
      messages: [{ role: "system", content: system }, { role: "user", content: user }],
      reasoning_effort: null, temperature: 0, max_tokens: maxTokens,
    }),
    signal: AbortSignal.timeout(6000),
  });
  if (!res.ok) throw new Error(`judge HTTP ${res.status}`);
  const j: any = await res.json();
  return String(j.choices?.[0]?.message?.content ?? "").trim().toUpperCase();
}

const ASK_SYS = `You help a phone assistant understand speech-to-text transcripts of an Indian user (Hindi, Bengali, English, mixed). The transcript may be wrong: words can be garbled or even shown in another Indian script (for example "राखी" or "રાખો" can be a mis-heard "rakho", which means "put down / hang up"). Judge by meaning. Answer with exactly one word: YES or NO.`;

/** The assistant had asked "shall I end the call?". Did the user agree (or ask to hang up)? */
export const agreesToEnd = async (apiKey: string, assistantSaid: string, userSaid: string): Promise<boolean> =>
  (await decide(apiKey, ASK_SYS + `
The assistant has just OFFERED to end the call. So any plain agreement is a YES, even a one-word "ok".
YES examples: "ओके राखो", "राखी" (mis-heard rakho), "हाँ", "हाँ कर दो", "ठीक है", "okay", "okay bye", "bye", "रख दो", "কেটে দাও", "হ্যাঁ রেখে দাও", "ઓકે રાખો", "thanks, bye", "haan kaato".
NO only when he refuses or adds something new: "नहीं", "रुको", "wait", "एक और काम है", "পরে", a new question or a new task.`,
    `Assistant said: "${assistantSaid}"\nUser replied: "${userSaid}"\nDid the user agree to end the call? YES or NO.`)).startsWith("YES");

/** The model wants to hang up. Did the user's latest message actually ask for the call to end? */
export const askedToEnd = async (apiKey: string, userSaid: string, assistantSaid = ""): Promise<boolean> =>
  (await decide(apiKey, ASK_SYS + `
On a phone call, a bare "रखो" / "रख दो" / "rakho" / "put it down" at the end of a message usually means HANG UP, especially right after he says nothing is missing or that he is done. It means something else only if an object is named ("note रखो", "reminder रखो").`,
    `${assistantSaid ? `The assistant had just said: "${assistantSaid.slice(0, 200)}"\n` : ""}User said: "${userSaid}"\nIs the user saying goodbye or asking to hang up / end / cut / put down the phone call (not just confirming a task or saying yes to something else)? If the message ALSO asks for something else (for example "note this and hang up now") but clearly includes the instruction to hang up, the answer is still YES. YES or NO.`)).startsWith("YES");

/** A long message: is it mainly a list of tasks/facts to remember (RECAP), or something to answer (ANSWER)? */
export const messageKind = async (apiKey: string, userSaid: string): Promise<"RECAP" | "ANSWER"> => {
  const r = await decide(apiKey, ASK_SYS.replace("Answer with exactly one word: YES or NO.", "Answer with exactly one word."),
    `User said: "${userSaid.slice(0, 1500)}"\nRECAP = the message mainly hands over several tasks, facts, times, people or reminders for the assistant to remember, or asks the assistant to repeat them.\nANSWER = the message asks the assistant a question, for an explanation, an opinion, advice, a role-play or help, or is casual chat (even if it also mentions a task).\nReply RECAP or ANSWER.`, 4);
  return r.startsWith("RECAP") ? "RECAP" : "ANSWER";
};

export type Route = "RECAP" | "ADVICE" | "RECALL" | "EXPERT" | "TASK" | "CHAT";
/** What kind of turn is this? RECAP = hands over several tasks/facts (or asks to repeat them); ADVICE = asks for a
 *  question answered, an opinion, an explanation, a role-play or help; CHAT = everything else (greeting, a short answer, a
 *  confirmation, one small task, small talk). */
export const routeTurn = async (apiKey: string, userSaid: string, assistantSaid = ""): Promise<Route> => {
  const r = await decide(apiKey, ASK_SYS.replace("Answer with exactly one word: YES or NO.", "Answer with exactly one word."),
    `${assistantSaid ? `The assistant had just said: "${assistantSaid.slice(0, 200)}"\n` : ""}User said: "${userSaid.slice(0, 1500)}"
RECAP = the message hands the assistant SEVERAL tasks, facts, times, people or reminders to remember, or asks the assistant to repeat/summarise what was said.
RECALL = the user asks what he said, asked, decided or was told on an EARLIER day or EARLIER call, or what is still pending/open/due from before, or how work he already handed over to the agent is going ("what did I ask yesterday about...", "last time...", "what is pending?", "how is it going?"). Asking to repeat what he said just now in this call is RECAP, not RECALL. A COMPLAINT or feedback about how the assistant behaved ("you did not call me when I asked", "that was wrong", "you should have...") is CHAT, never RECALL: the assistant should simply acknowledge it.
TASK = he asks for SOMETHING TO BE DONE on the computer by his agent: build, make, create, design, fix, change, update, write, check, research or set up something (a website, an app, a page, a feature, a bug fix, a document). It counts in ANY words, even a short "make me a website", "ek landing page bana do", "ওয়েবসাইট বানিয়ে দাও", "fix the refund bug". He does not need to mention an agent. It is NOT a task when he only asks a question, wants an opinion, chats, gives a reminder or note for himself, or says a plan without wanting it done.
EXPERT = he needs a PRECISE, factual or technical answer, a calculation, a debugging answer, or a check about his own projects, code, files or apps that a casual opinion cannot give ("how do I fix this error", "what exactly does our refund code do", "calculate the GST on 1,25,000", "why is my server returning 502"). Business judgement, sales and opinions are ADVICE, not EXPERT.
ADVICE = the user asks a question, wants an opinion, an explanation, advice, a check of his plan, help with a problem he describes ("my server has been down since last night, what do I do?" is ADVICE even though it mentions a day), or asks the assistant to play a role (for example a salesperson).
CHAT = anything else: greeting, small talk, a short answer or confirmation (yes/no/okay), one small task or note, or a CORRECTION of something said earlier ("not X, Y", "change it to...").
RECAP needs at least three separate items in one message.
Reply with RECAP, ADVICE, RECALL, EXPERT, TASK or CHAT.`, 5);
  return r.startsWith("RECAP") ? "RECAP" : r.startsWith("ADVICE") ? "ADVICE" : r.startsWith("RECALL") ? "RECALL" : r.startsWith("EXPERT") ? "EXPERT" : r.startsWith("TASK") ? "TASK" : "CHAT";
};

/** Hand-off approval. `offered` = the assistant had just asked "shall I send this to my agent?"; otherwise he must have asked for it himself.
 *  Judged by meaning, like the hang-up: a mis-heard "yes" is still a yes, a new task or "wait" is not. */
export const approvesHandoff = async (apiKey: string, userSaid: string, assistantSaid: string, offered: boolean): Promise<boolean> =>
  (await decide(apiKey, ASK_SYS + (offered
    ? `
The assistant has just OFFERED to send the task to his coding/computer agent. A plain agreement is a YES, even one word.
YES: "हाँ भेज दो", "ok send it", "कर दो", "हाँ", "ठीक है", "haan pathiye dao", "go ahead", "yes".
NO: "नहीं", "रुको", "wait", "एक चीज़ और बदलो", "not yet", a correction, or a new question or task.`
    : `
The user may be telling the assistant to GET HIS AGENT TO DO A JOB. Answer YES when he asks the assistant to tell, send, pass, give or hand a job to the agent, or to have the agent build / make / fix / check something: "तुम डीप एजेंट को बोलो वेबसाइट बनाए", "go tell the deep agent to build a cookie website", "agent को दे दो", "भेज दो", "এজেন্টকে বলো", "start the agent on it". The agent is software (Deep Agent, डीप एजेंट, my agent): never a person.
Answer NO for: a question about the agent ("how is the agent doing?"), notes, reminders or tasks meant for the assistant itself, "ok", "thanks", chat, or a plain description of a plan without asking for it to be started.`),
    `${assistantSaid ? `The assistant had just said: "${assistantSaid.slice(0, 220)}"\n` : ""}User said: "${userSaid}"\nDid the user approve sending the work to the agent now? YES or NO.`)).startsWith("YES");

/** A simpler second opinion for the case the router is unsure about: is he ASKING for something to be built / made / done for him? (a plain yes/no is far steadier than a five-way choice) */
export const wantsWorkDone = async (apiKey: string, userSaid: string): Promise<boolean> =>
  (await decide(apiKey, ASK_SYS + `
Question: does the user ask for something to be BUILT, MADE, CREATED, DESIGNED, FIXED, CHANGED, SET UP or DONE for him (by his assistant or agent)? It counts when phrased as a need or a wish ("मुझे एक वेबसाइट चाहिए", "I need a landing page", "आমার একটা অ্যাপ চাই") or as a command ("वेबसाइट बना दो", "fix the bug").
YES: "मुझे एक वेबसाइट चाहिए जो कुकीज़ बेचती हो", "make me a logo", "रिफंड बग ठीक कर दो", "আমার একটা ওয়েবসাইট চাই".
NO: a question ("वेबसाइट का प्राइस कितना रखें?"), asking for an opinion or advice, chat or greeting, a note or reminder for himself, a meeting or payment to remember, or asking what was said before.`,
    `User said: "${userSaid.slice(0, 600)}"\nDoes he ask for something to be built, made or done? YES or NO.`)).startsWith("YES");
