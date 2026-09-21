# SPP Audio Studio for Windows

WinUI 3 / Windows App SDK UI scaffold for the Windows port. The macOS implementation remains untouched.

## Projects

- `src/SPPAudioStudio.Core` — platform-neutral command, task, validation, and worker-state abstractions.
- `src/SPPAudioStudio.Windows` — unpackaged x64 WinUI 3 desktop app.
- `tests/SPPAudioStudio.Core.Tests` — MSTest coverage for core behavior.

## Build

```bash
dotnet test windows/SPPAudioStudio.Windows.sln -c Release
dotnet build windows/src/SPPAudioStudio.Windows/SPPAudioStudio.Windows.csproj -c Release -p:Platform=x64
```

The unpackaged app requires the Windows App Runtime 1.6 (x64). The current runtime deliberately reports **“Windows Worker 尚未集成”**. It does not present sample engine/model state as real runtime state.
