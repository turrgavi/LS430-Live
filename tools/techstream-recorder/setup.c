/*
 * LS430-Logger.exe: installs / removes the LS430 Logger J2534 recorder and
 * lets you type timestamped notes while you record.
 *
 * Install = copy ls430_logger.dll (embedded in this exe) to C:\LS430Logger and
 * add a new J2534 device "LS430 Logger" to the registry. It copies the real
 * Mini VCI entry's settings and remembers the real driver's path, so the logger
 * can pass every call through. The real Mini VCI entry is never changed.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define ROOT     "SOFTWARE\\PassThruSupport.04.04"
#define OUR_NAME "LS430 Logger"
#define OUR_KEY  ROOT "\\" OUR_NAME
#define BASE_DIR "C:\\LS430Logger"
#define LOG_DIR  BASE_DIR "\\logs"
#define DLL_PATH BASE_DIR "\\ls430_logger.dll"
#define WOW      KEY_WOW64_32KEY

typedef struct { char key[256], name[256], lib[MAX_PATH]; } Dev;

static void pause_exit(int code)
{
    printf("\nPress Enter to close this window.");
    fflush(stdout);
    getchar();
    exit(code);
}

static int read_str(HKEY k, const char *val, char *out, DWORD cap)
{
    DWORD type, n = cap;
    if (RegQueryValueExA(k, val, NULL, &type, (BYTE *)out, &n) != ERROR_SUCCESS) return 0;
    out[cap - 1] = 0;
    return type == REG_SZ || type == REG_EXPAND_SZ;
}

static int list_devices(Dev *d, int max)
{
    HKEY root, k;
    int n = 0;
    DWORD i;
    char name[256];
    if (RegOpenKeyExA(HKEY_LOCAL_MACHINE, ROOT, 0, KEY_READ | WOW, &root) != ERROR_SUCCESS)
        return 0;
    for (i = 0; n < max; i++) {
        DWORD len = sizeof name;
        if (RegEnumKeyExA(root, i, name, &len, NULL, NULL, NULL, NULL) != ERROR_SUCCESS) break;
        if (!_stricmp(name, OUR_NAME)) continue;
        if (RegOpenKeyExA(root, name, 0, KEY_READ | WOW, &k) != ERROR_SUCCESS) continue;
        strcpy(d[n].key, name);
        if (!read_str(k, "Name", d[n].name, sizeof d[n].name)) strcpy(d[n].name, name);
        if (read_str(k, "FunctionLibrary", d[n].lib, sizeof d[n].lib)) n++;
        RegCloseKey(k);
    }
    RegCloseKey(root);
    return n;
}

static int looks_like_vci(const Dev *d)
{
    char s[1024];
    int i;
    snprintf(s, sizeof s, "%s %s %s", d->key, d->name, d->lib);
    for (i = 0; s[i]; i++) s[i] = (char)tolower((unsigned char)s[i]);
    return strstr(s, "mvci") || strstr(s, "mini") || strstr(s, "xhorse") ||
           strstr(s, "vci") || strstr(s, "toyota");
}

static int extract_dll(void)
{
    HRSRC r = FindResourceA(NULL, "LOGGERDLL", (LPCSTR)RT_RCDATA);
    HGLOBAL g;
    DWORD size, w;
    HANDLE f;
    if (!r || !(g = LoadResource(NULL, r))) return 0;
    size = SizeofResource(NULL, r);
    CreateDirectoryA(BASE_DIR, NULL);
    CreateDirectoryA(LOG_DIR, NULL);
    f = CreateFileA(DLL_PATH, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return 0;
    WriteFile(f, LockResource(g), size, &w, NULL);
    CloseHandle(f);
    return w == size;
}

static int set_str(HKEY k, const char *name, const char *v)
{
    return RegSetValueExA(k, name, 0, REG_SZ, (const BYTE *)v, (DWORD)strlen(v) + 1) == ERROR_SUCCESS;
}

static void do_install(void)
{
    Dev d[32];
    int n = list_devices(d, 32), pick = -1, i, cands = 0;
    HKEY src, dst;
    DWORD idx;

    printf("\nJ2534 devices found on this computer:\n");
    for (i = 0; i < n; i++) {
        printf("  %d. %s\n     %s\n", i + 1, d[i].name, d[i].lib);
        if (looks_like_vci(&d[i])) { cands++; if (pick < 0) pick = i; }
    }
    if (n == 0) {
        printf("  (none)\n\nNo J2534 driver is installed. Install the Mini VCI driver first\n"
               "(the one Techstream already uses), then run this again.\n");
        pause_exit(1);
    }
    if (n == 1) pick = 0;
    if (pick < 0 || cands > 1) {
        char line[32];
        printf("\nWhich one is your Mini VCI? Type its number and press Enter: ");
        fflush(stdout);
        if (!fgets(line, sizeof line, stdin)) pause_exit(1);
        pick = atoi(line) - 1;
        if (pick < 0 || pick >= n) { printf("Not a valid number.\n"); pause_exit(1); }
    }
    printf("\nRecording through: %s\n", d[pick].name);

    if (!extract_dll()) {
        printf("Could not write %s (error %lu). Is Techstream still open? Close it and retry.\n",
               DLL_PATH, GetLastError());
        pause_exit(1);
    }

    /* Copy every value of the real device's key, then point it at our DLL. */
    {
        char key[512];
        snprintf(key, sizeof key, ROOT "\\%s", d[pick].key);
        if (RegOpenKeyExA(HKEY_LOCAL_MACHINE, key, 0, KEY_READ | WOW, &src) != ERROR_SUCCESS) {
            printf("Could not read the Mini VCI's registry entry.\n");
            pause_exit(1);
        }
    }
    if (RegCreateKeyExA(HKEY_LOCAL_MACHINE, OUR_KEY, 0, NULL, 0, KEY_ALL_ACCESS | WOW, NULL,
                        &dst, NULL) != ERROR_SUCCESS) {
        printf("Could not write to the registry. Right-click LS430-Logger.exe and choose\n"
               "\"Run as administrator\".\n");
        pause_exit(1);
    }
    for (idx = 0;; idx++) {
        char name[256];
        BYTE data[4096];
        DWORD nl = sizeof name, dl = sizeof data, type;
        if (RegEnumValueA(src, idx, name, &nl, NULL, &type, data, &dl) != ERROR_SUCCESS) break;
        RegSetValueExA(dst, name, 0, type, data, dl);
    }
    RegCloseKey(src);
    set_str(dst, "Name", "LS430 Logger (records Mini VCI)");
    set_str(dst, "Vendor", "LS430 Live");
    set_str(dst, "FunctionLibrary", DLL_PATH);
    set_str(dst, "RealFunctionLibrary", d[pick].lib);
    RegCloseKey(dst);

    printf("\nInstalled.\n\n"
           "Next: open Techstream, go to Setup > VIM Select, choose\n"
           "\"LS430 Logger (records Mini VCI)\", click OK, then use Techstream as normal.\n"
           "Recordings go to %s\n", LOG_DIR);
}

static void do_uninstall(void)
{
    LONG r = RegDeleteKeyExA(HKEY_LOCAL_MACHINE, OUR_KEY, WOW, 0);
    if (r == ERROR_SUCCESS || r == ERROR_FILE_NOT_FOUND)
        printf("\nRemoved the LS430 Logger device. In Techstream, set Setup > VIM Select back\n"
               "to your Mini VCI. Your recordings in %s were kept.\n", LOG_DIR);
    else
        printf("\nCould not remove it (error %ld). Try \"Run as administrator\".\n", r);
}

static void do_notes(void)
{
    char path[MAX_PATH], line[512];
    SYSTEMTIME t;
    FILE *f;
    CreateDirectoryA(BASE_DIR, NULL);
    CreateDirectoryA(LOG_DIR, NULL);
    GetLocalTime(&t);
    snprintf(path, sizeof path, LOG_DIR "\\notes-%04d%02d%02d.txt", t.wYear, t.wMonth, t.wDay);
    printf("\nNotes mode. Before each step in Techstream, type what you are about to do\n"
           "(for example: fan ON) and press Enter. Each note gets the exact time.\n"
           "Type q and press Enter when finished.\n\n");
    for (;;) {
        printf("note> ");
        fflush(stdout);
        if (!fgets(line, sizeof line, stdin)) break;
        line[strcspn(line, "\r\n")] = 0;
        if (!_stricmp(line, "q")) break;
        if (!line[0]) continue;
        GetLocalTime(&t);
        if ((f = fopen(path, "a"))) {
            fprintf(f, "%02d:%02d:%02d.%03d NOTE %s\n", t.wHour, t.wMinute, t.wSecond,
                    t.wMilliseconds, line);
            fclose(f);
            printf("  saved at %02d:%02d:%02d\n", t.wHour, t.wMinute, t.wSecond);
        } else {
            printf("  could not save to %s\n", path);
        }
    }
}

int main(void)
{
    char line[32];
    printf("LS430 Logger 1.0: records what Techstream says to the car.\n"
           "It only listens. It never sends anything of its own.\n\n"
           "  1. Install (add \"LS430 Logger\" to Techstream's VIM list)\n"
           "  2. Take notes while recording\n"
           "  3. Open the recordings folder\n"
           "  4. Uninstall\n\n"
           "Type a number and press Enter: ");
    fflush(stdout);
    if (!fgets(line, sizeof line, stdin)) return 1;
    switch (atoi(line)) {
    case 1: do_install(); break;
    case 2: do_notes(); return 0;
    case 3:
        CreateDirectoryA(BASE_DIR, NULL);
        CreateDirectoryA(LOG_DIR, NULL);
        ShellExecuteA(NULL, "open", LOG_DIR, NULL, NULL, SW_SHOWNORMAL);
        return 0;
    case 4: do_uninstall(); break;
    default: printf("Nothing done.\n");
    }
    pause_exit(0);
    return 0;
}
