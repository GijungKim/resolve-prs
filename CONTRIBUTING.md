# Contributing

Thanks for helping make resolve-prs smarter. The most valuable contributions are new breaking change patterns.

## Adding a breaking change pattern

If you've hit a dependency upgrade that broke things and figured out the fix, add it to the knowledge base.

### Format

Add a single line under the appropriate subsection in `skills/resolve-prs/SKILL.md`:

```
- **package-name X -> Y**: Brief description of what changed and how to fix it.
```

### Guidelines

- Keep it to one line. Link to a migration guide if more detail is needed.
- Make it generalizable. It should help any project hitting this upgrade, not just yours.
- Include the version range (e.g., "3 -> 4", "8 -> 9+").
- Mention the specific API change and the fix (e.g., "`new Foo()` -> `createFoo()`").
- Put it in the right subsection (JS/TS, React/RN, Python, General) or create a new one if needed.

### Example

Good:
```
- **react-native-mmkv 3 -> 4**: Constructor changed from `new MMKV()` to `createMMKV()`, `.delete()` renamed to `.remove()`, requires Nitro Modules jest mock.
```

Bad:
```
- Fixed storage import in my project after upgrading mmkv.
```

## Other contributions

- Bug fixes to the skill logic are welcome
- New flags or workflow improvements - open an issue first to discuss
- Keep the SKILL.md under a reasonable size so it doesn't blow up context windows
