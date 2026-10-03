# Cumulative patch locator

The exact reviewable cumulative patch is generated from this bound source snapshot with:

```sh
git diff --binary df04060f189a10bb81baf522a58347cddc6cc915
```

At package creation, the binary diff stream SHA-256 was `27e4d5bbfdb1ef580ba75caf5a3fb2af9c9a55cea7280a85ea4fe2130bc90a09`. It intentionally includes the previously applied batch-3 implementation plus this batch's changes and excludes untracked review evidence. No commit was created.
