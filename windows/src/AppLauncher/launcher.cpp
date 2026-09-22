#include <windows.h>
#include <string>

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int)
{
    wchar_t modulePath[MAX_PATH]{};
    const DWORD length = GetModuleFileNameW(nullptr, modulePath, MAX_PATH);
    if (length == 0 || length >= MAX_PATH) return 2;

    std::wstring root(modulePath, length);
    const auto slash = root.find_last_of(L"\\/");
    if (slash == std::wstring::npos) return 3;
    root.resize(slash + 1);

    const std::wstring appDir = root + L"app";
    const std::wstring target = appDir + L"\\SPPAudioStudio.Windows.exe";
    std::wstring commandLine = L"\"" + target + L"\"";

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(target.c_str(), commandLine.data(), nullptr, nullptr, FALSE, 0,
                        nullptr, appDir.c_str(), &startup, &process))
    {
        MessageBoxW(nullptr, L"Could not start app\\SPPAudioStudio.Windows.exe. Please keep the program folder intact.",
                    L"SPP Audio Studio", MB_OK | MB_ICONERROR);
        return 4;
    }

    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
