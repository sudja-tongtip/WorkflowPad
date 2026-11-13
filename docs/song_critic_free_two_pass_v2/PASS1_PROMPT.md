# PASS1_PROMPT (arm B, first request of the session)

The user message = the song data block + the task text below. The song data block is the one production builds for the Song Critic (title `Test Song 01`/`Test Song 03`, style, lyrics with line ids, the program-counted structure and the lines that return, `REPLY LANGUAGE: Thai`) WITHOUT its report request; it is in `RAW_REQUESTS/*_twopass_pass1_request.json` (`user_message`). The lyrics are sent once, here.

Task text (exactly as in the brief):

```
Read the complete lyrics and critique the song as a
songwriting work.

Use your own judgment to identify what matters most.

Explain your assessment with evidence from the lyrics.
Do not follow a fixed checklist or force any particular
number of positive or negative findings.
```
