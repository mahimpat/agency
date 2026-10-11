# Safeguarding Policy: Letters from History (Forem Innovations)

*Adopted: [date]. Review every year, and after any incident. Have it checked by a safeguarding professional or solicitor. Free and low-cost help is available from [NSPCC Learning](https://learning.nspcc.org.uk/).*

Children write to us. Some will tell their pen pal things they haven't told anyone else. This policy sets out how we notice that, and what we do, every time.

## 1. Who is responsible

- **Designated Safeguarding Lead (DSL):** [name, contact]. Responsible for decisions on held replies and for referrals.
- **Deputy DSL:** [name, contact]. Covers when the DSL is away. Someone must be able to act within **one working day** at all times.
- Anyone who reviews children's letters should:
  - complete basic safeguarding training (for example NSPCC Learning's introductory course),
  - have a basic DBS check,
  - read this policy.

## 2. How the system protects children

| Layer | What it does |
|---|---|
| Parents only | Only a parent or guardian (or someone with their permission) can sign a child up and upload replies. Children have no accounts and no direct contact with anyone. |
| No contact details | Letters never ask for personal details. Personal details in replies (addresses, phone numbers, emails, social media) are removed automatically before anything is stored or written. |
| Two independent checks on every reply | 1. An AI safeguarding assessment grades risk as none, low, high or urgent. 2. A rule-based screen for worrying words (harm, self-harm, secrets, fear of an adult, running away, neglect, bullying) **forces a hold even if the AI says the letter is fine**. |
| Hold, don't reply | A held reply gets **no automatic answer**. The DSL is emailed straight away, with URGENT in the subject when the risk is urgent. The photo is kept until a person has reviewed it. |
| Instructions ignored | Replies that try to instruct the AI, or that don't look written by a child, are held. Letters treat a child's words as something to answer, never as commands. |
| Output checks | Every outgoing letter is fact-checked, including for child safety. It must not repeat personal details, promise meetings, gifts or calls, suggest secrets from parents, or respond like a counsellor. |
| Human approval | At launch, a person approves every letter before it's posted (`REVIEW_ALL=true`). |

The automatic checks help a person notice. They don't replace a person's judgement. False alarms are expected and fine.

## 3. What counts as a concern

- **Urgent (possible immediate danger):** "he hurts me every night", "I'm going to hurt myself", "I'm running away tomorrow", or a child left alone or without food.
- **High (a trusted adult must act):**
  - any hint of physical, emotional or sexual abuse or neglect;
  - fear of someone at home or school;
  - bullying;
  - self-harm or wanting to die;
  - an adult asking the child to keep secrets;
  - sexual content;
  - serious distress.
- **Low (answer kindly, no hold):** a pet died, nerves about a test, missing a friend. The letter acknowledges it briefly and gently suggests talking to a grown-up they trust.

## 4. What to do with a held reply

1. **Read it the same day.** Open the original photo from `/admin` (it's kept until you release or dismiss the reply).
2. **Immediate danger to a child:** call **999**.
3. **Worried about abuse, neglect or harm:** contact the child's **local council children's services** (find the right council at [gov.uk/report-child-abuse-to-local-council](https://www.gov.uk/report-child-abuse-to-local-council)), using the address on the account. For advice, call the **NSPCC helpline on 0808 800 5000** or email help@nspcc.org.uk. If you're unsure whether to refer, ask; that's what the helpline is for.
4. **Lower-level worries** (e.g. bullying, sadness): contact the parent kindly and privately. **Do not contact the parent first if they might be part of the concern**; follow step 3 instead.
5. **Never** write to the child about what they disclosed, investigate yourself, or promise to keep a secret.
6. **Decide in /admin:**
   - **Release** only for clear false alarms (e.g. "my brother hit me with a pillow in a game"). The answer is still checked before posting.
   - Otherwise **Dismiss**, so no automatic letter is written.
   - Consider pausing letters to that child while the concern is handled.
7. **Record it** (section 5).

## 5. Records

For every held reply, record:
- the date and time,
- the reply number,
- what it said (summary),
- your decision,
- who you contacted and when, and what they advised,
- any follow-up.

Keep these records securely and separately from marketing data, with access limited to the DSL and deputy. Keep them for as long as needed to protect the child, then delete them securely.

## 6. Allegations about us

If anyone raises a concern about a member of staff or contractor in relation to children, the DSL (or the deputy, if the concern is about the DSL) contacts the **Local Authority Designated Officer (LADO)** for the child's area within one working day.

## 7. Online safety and data

- Children never get accounts, messages or contact with staff.
- Only the DSL, the deputy and named reviewers can use `/admin`, each with their own account and a strong password.
- Photos of ordinary replies are deleted as soon as they've been read. Photos of held replies are deleted after review.
- Personal data is handled as set out in the [privacy policy](../app/templates/privacy.html).

## 8. Useful contacts

| Who | When | Contact |
|---|---|---|
| Police | A child is in immediate danger | **999** |
| Local council children's services | Concerns about abuse or neglect | [gov.uk/report-child-abuse-to-local-council](https://www.gov.uk/report-child-abuse-to-local-council) |
| NSPCC helpline (adults) | Advice on any concern | **0808 800 5000** · help@nspcc.org.uk |
| Childline (for children) | Can be suggested to a child by a trusted adult, never by our letters | 0800 1111 |
| LADO | Allegations about staff or contractors | Via the child's local council |
