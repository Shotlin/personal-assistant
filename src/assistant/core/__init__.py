"""sani-core: the Sani desktop sidecar (Sani master doc 13/14).

A bundled Python process the Tauri host owns over private stdin/stdout
framed-JSON IPC -- never a localhost web server. This package holds the
transport, the typed protocol, the small agent registry (Sani master doc
9: ids, name, capabilities, run, cancel -- no workflow platform) and the
run lifecycle with cancellation. Real agents are wired in a later
milestone; this milestone is the skeleton with fake agents in tests.
"""
