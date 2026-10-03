"""Claude Code companion: Sani drives the user's own Claude Code like a person.

Nothing here embeds Claude Code or touches an API key. Sani runs the installed
``claude`` program in non-interactive mode (``claude -p``) under the user's own
login, streams what it does into the chat, supervises the run so it cannot burn
tokens in a loop, and hands a short summary back to the Deep Agent.
"""
