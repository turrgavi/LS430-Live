/*
 * LS430 Logger: a J2534 (PassThru 04.04) recording shim.
 *
 * Techstream loads this DLL as if it were a J2534 cable driver. Every call is
 * passed straight through to the real Mini VCI driver, and a copy of what was
 * sent and received is written to C:\LS430Logger\logs\capture-*.txt.
 * It never invents or changes any message: it only records.
 *
 * Build (32-bit, the only kind Techstream can load):
 *   i686-w64-mingw32-gcc -O2 -shared -o ls430_logger.dll logger.c logger.def \
 *     -static-libgcc -Wl,--kill-at
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#define OUR_KEY  "SOFTWARE\\PassThruSupport.04.04\\LS430 Logger"
#define BASE_DIR "C:\\LS430Logger"
#define LOG_DIR  BASE_DIR "\\logs"

typedef struct {
    unsigned long ProtocolID, RxStatus, TxFlags, Timestamp, DataSize, ExtraDataIndex;
    unsigned char Data[4128];
} PASSTHRU_MSG;
typedef struct { unsigned long Parameter, Value; } SCONFIG;
typedef struct { unsigned long NumOfParams; SCONFIG *ConfigPtr; } SCONFIG_LIST;
typedef struct { unsigned long NumOfBytes; unsigned char *BytePtr; } SBYTE_ARRAY;

typedef long (WINAPI *fOpen)(void *, unsigned long *);
typedef long (WINAPI *fClose)(unsigned long);
typedef long (WINAPI *fConnect)(unsigned long, unsigned long, unsigned long, unsigned long, unsigned long *);
typedef long (WINAPI *fDisconnect)(unsigned long);
typedef long (WINAPI *fReadMsgs)(unsigned long, PASSTHRU_MSG *, unsigned long *, unsigned long);
typedef long (WINAPI *fWriteMsgs)(unsigned long, PASSTHRU_MSG *, unsigned long *, unsigned long);
typedef long (WINAPI *fStartPeriodic)(unsigned long, PASSTHRU_MSG *, unsigned long *, unsigned long);
typedef long (WINAPI *fStopPeriodic)(unsigned long, unsigned long);
typedef long (WINAPI *fStartFilter)(unsigned long, unsigned long, PASSTHRU_MSG *, PASSTHRU_MSG *, PASSTHRU_MSG *, unsigned long *);
typedef long (WINAPI *fStopFilter)(unsigned long, unsigned long);
typedef long (WINAPI *fSetProgV)(unsigned long, unsigned long, unsigned long);
typedef long (WINAPI *fReadVersion)(unsigned long, char *, char *, char *);
typedef long (WINAPI *fGetLastError)(char *);
typedef long (WINAPI *fIoctl)(unsigned long, unsigned long, void *, void *);

static struct {
    fOpen Open; fClose Close; fConnect Connect; fDisconnect Disconnect;
    fReadMsgs ReadMsgs; fWriteMsgs WriteMsgs; fStartPeriodic StartPeriodic;
    fStopPeriodic StopPeriodic; fStartFilter StartFilter; fStopFilter StopFilter;
    fSetProgV SetProgV; fReadVersion ReadVersion; fGetLastError GetLastErr; fIoctl Ioctl;
} R;

static HMODULE g_real;
static HANDLE g_log = INVALID_HANDLE_VALUE;
static CRITICAL_SECTION g_cs;
static char g_err[256] = "";
static volatile LONG g_inited;
static unsigned long g_proto[64];   /* protocol per channel id, for nicer logs */

#define ERR_FAILED 0x07
#define ERR_BUFFER_EMPTY 0x10

/* ------------------------------------------------------------------ logging */

static void log_raw(const char *s)
{
    DWORD n;
    if (g_log != INVALID_HANDLE_VALUE)
        WriteFile(g_log, s, (DWORD)strlen(s), &n, NULL);
}

static void logf_(const char *fmt, ...)
{
    char buf[12000];
    SYSTEMTIME t;
    int k;
    va_list ap;
    GetLocalTime(&t);
    k = snprintf(buf, sizeof buf, "%02d:%02d:%02d.%03d ",
                 t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
    va_start(ap, fmt);
    vsnprintf(buf + k, sizeof buf - k - 2, fmt, ap);
    va_end(ap);
    strcat(buf, "\r\n");
    EnterCriticalSection(&g_cs);
    log_raw(buf);
    LeaveCriticalSection(&g_cs);
}

static void hex(char *out, size_t cap, const unsigned char *d, unsigned long n)
{
    size_t k = 0;
    unsigned long i;
    out[0] = 0;
    if (!d) { snprintf(out, cap, "(null)"); return; }
    for (i = 0; i < n && k + 4 < cap; i++)
        k += snprintf(out + k, cap - k, i ? " %02X" : "%02X", d[i]);
}

static const char *proto_name(unsigned long p)
{
    switch (p) {
    case 1: return "J1850VPW"; case 2: return "J1850PWM"; case 3: return "ISO9141";
    case 4: return "ISO14230"; case 5: return "CAN"; case 6: return "ISO15765";
    case 7: return "SCI_A_ENGINE"; case 8: return "SCI_A_TRANS";
    case 9: return "SCI_B_ENGINE"; case 10: return "SCI_B_TRANS";
    default: return "OTHER";
    }
}

static const char *ioctl_name(unsigned long id)
{
    switch (id) {
    case 1: return "GET_CONFIG"; case 2: return "SET_CONFIG"; case 3: return "READ_VBATT";
    case 4: return "FIVE_BAUD_INIT"; case 5: return "FAST_INIT";
    case 7: return "CLEAR_TX_BUFFER"; case 8: return "CLEAR_RX_BUFFER";
    case 9: return "CLEAR_PERIODIC_MSGS"; case 10: return "CLEAR_MSG_FILTERS";
    case 11: return "CLEAR_FUNCT_MSG_LOOKUP_TABLE"; case 12: return "ADD_TO_FUNCT_MSG_LOOKUP_TABLE";
    case 13: return "DELETE_FROM_FUNCT_MSG_LOOKUP_TABLE"; case 14: return "READ_PROG_VOLTAGE";
    default: return "VENDOR";
    }
}

static void log_msg(const char *tag, unsigned long ch, const PASSTHRU_MSG *m)
{
    char h[4128 * 3 + 16];
    if (!m) { logf_("%s ch=%lu (null)", tag, ch); return; }
    hex(h, sizeof h, m->Data, m->DataSize > 4128 ? 4128 : m->DataSize);
    logf_("%s ch=%lu proto=%s rx=0x%lX tx=0x%lX ts=%lu len=%lu data=%s",
          tag, ch, proto_name(m->ProtocolID), m->RxStatus, m->TxFlags,
          m->Timestamp, m->DataSize, h);
}

/* ------------------------------------------------------------ registry/init */

static int reg_get(HKEY root, const char *key, const char *val, char *out, DWORD cap)
{
    HKEY k;
    DWORD type, n = cap;
    LONG r = RegOpenKeyExA(root, key, 0, KEY_READ, &k);
    if (r != ERROR_SUCCESS) return 0;
    r = RegQueryValueExA(k, val, NULL, &type, (BYTE *)out, &n);
    RegCloseKey(k);
    if (r != ERROR_SUCCESS || type != REG_SZ) return 0;
    out[cap - 1] = 0;
    return 1;
}

static void open_log(void)
{
    char path[MAX_PATH];
    SYSTEMTIME t;
    CreateDirectoryA(BASE_DIR, NULL);
    CreateDirectoryA(LOG_DIR, NULL);
    GetLocalTime(&t);
    snprintf(path, sizeof path, LOG_DIR "\\capture-%04d%02d%02d-%02d%02d%02d.txt",
             t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
    g_log = CreateFileA(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                        NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
}

#define LOAD(field, name) R.field = (void *)GetProcAddress(g_real, name)

static void init(void)
{
    char real[MAX_PATH] = "";
    char exe[MAX_PATH] = "";
    if (InterlockedExchange(&g_inited, 1)) return;
    open_log();
    GetModuleFileNameA(NULL, exe, sizeof exe);
    logf_("LS430 Logger 1.0 started. Program: %s", exe);
    if (!reg_get(HKEY_LOCAL_MACHINE, OUR_KEY, "RealFunctionLibrary", real, sizeof real)) {
        snprintf(g_err, sizeof g_err, "LS430 Logger: not installed (run LS430-Logger.exe, choose Install)");
        logf_("ERROR %s", g_err);
        return;
    }
    g_real = LoadLibraryA(real);
    if (!g_real) {
        snprintf(g_err, sizeof g_err, "LS430 Logger: could not load real driver %s (error %lu)",
                 real, GetLastError());
        logf_("ERROR %s", g_err);
        return;
    }
    LOAD(Open, "PassThruOpen"); LOAD(Close, "PassThruClose");
    LOAD(Connect, "PassThruConnect"); LOAD(Disconnect, "PassThruDisconnect");
    LOAD(ReadMsgs, "PassThruReadMsgs"); LOAD(WriteMsgs, "PassThruWriteMsgs");
    LOAD(StartPeriodic, "PassThruStartPeriodicMsg"); LOAD(StopPeriodic, "PassThruStopPeriodicMsg");
    LOAD(StartFilter, "PassThruStartMsgFilter"); LOAD(StopFilter, "PassThruStopMsgFilter");
    LOAD(SetProgV, "PassThruSetProgrammingVoltage"); LOAD(ReadVersion, "PassThruReadVersion");
    LOAD(GetLastErr, "PassThruGetLastError"); LOAD(Ioctl, "PassThruIoctl");
    logf_("Real driver loaded: %s", real);
}

#define NEED(f) do { init(); if (!R.f) { if (!g_err[0]) snprintf(g_err, sizeof g_err, \
    "LS430 Logger: real driver has no " #f); return ERR_FAILED; } } while (0)

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r)
{
    (void)h; (void)r;
    if (reason == DLL_PROCESS_ATTACH) InitializeCriticalSection(&g_cs);
    if (reason == DLL_PROCESS_DETACH && g_log != INVALID_HANDLE_VALUE) {
        logf_("Logger unloaded");
        CloseHandle(g_log);
        g_log = INVALID_HANDLE_VALUE;
    }
    return TRUE;
}

/* ---------------------------------------------------------- PassThru API */

long WINAPI PassThruOpen(void *pName, unsigned long *pDeviceID)
{
    long r;
    NEED(Open);
    r = R.Open(pName, pDeviceID);
    logf_("Open name=%s -> ret=%ld dev=%lu", pName ? (char *)pName : "(null)", r,
          pDeviceID ? *pDeviceID : 0);
    return r;
}

long WINAPI PassThruClose(unsigned long dev)
{
    long r;
    NEED(Close);
    r = R.Close(dev);
    logf_("Close dev=%lu -> ret=%ld", dev, r);
    return r;
}

long WINAPI PassThruConnect(unsigned long dev, unsigned long proto, unsigned long flags,
                            unsigned long baud, unsigned long *pCh)
{
    long r;
    NEED(Connect);
    r = R.Connect(dev, proto, flags, baud, pCh);
    if (pCh && *pCh < 64) g_proto[*pCh] = proto;
    logf_("Connect dev=%lu proto=%s(%lu) flags=0x%lX baud=%lu -> ret=%ld ch=%lu", dev,
          proto_name(proto), proto, flags, baud, r, pCh ? *pCh : 0);
    return r;
}

long WINAPI PassThruDisconnect(unsigned long ch)
{
    long r;
    NEED(Disconnect);
    r = R.Disconnect(ch);
    logf_("Disconnect ch=%lu -> ret=%ld", ch, r);
    return r;
}

long WINAPI PassThruReadMsgs(unsigned long ch, PASSTHRU_MSG *m, unsigned long *pNum,
                             unsigned long timeout)
{
    long r;
    unsigned long i, want = pNum ? *pNum : 0;
    NEED(ReadMsgs);
    r = R.ReadMsgs(ch, m, pNum, timeout);
    if (pNum && *pNum > 0 && m) {
        for (i = 0; i < *pNum && i < want; i++) log_msg("RX", ch, &m[i]);
    } else if (r != 0 && r != ERR_BUFFER_EMPTY && r != 0x09 /* ERR_TIMEOUT */) {
        logf_("ReadMsgs ch=%lu -> ret=%ld", ch, r);
    }
    return r;
}

long WINAPI PassThruWriteMsgs(unsigned long ch, PASSTHRU_MSG *m, unsigned long *pNum,
                              unsigned long timeout)
{
    long r;
    unsigned long i, n = pNum ? *pNum : 0;
    NEED(WriteMsgs);
    for (i = 0; m && i < n; i++) log_msg("TX", ch, &m[i]);
    r = R.WriteMsgs(ch, m, pNum, timeout);
    if (r != 0) logf_("WriteMsgs ch=%lu -> ret=%ld sent=%lu", ch, r, pNum ? *pNum : 0);
    return r;
}

long WINAPI PassThruStartPeriodicMsg(unsigned long ch, PASSTHRU_MSG *m, unsigned long *pId,
                                     unsigned long interval)
{
    long r;
    NEED(StartPeriodic);
    log_msg("PERIODIC", ch, m);
    r = R.StartPeriodic(ch, m, pId, interval);
    logf_("StartPeriodicMsg ch=%lu every=%lums -> ret=%ld id=%lu", ch, interval, r,
          pId ? *pId : 0);
    return r;
}

long WINAPI PassThruStopPeriodicMsg(unsigned long ch, unsigned long id)
{
    long r;
    NEED(StopPeriodic);
    r = R.StopPeriodic(ch, id);
    logf_("StopPeriodicMsg ch=%lu id=%lu -> ret=%ld", ch, id, r);
    return r;
}

long WINAPI PassThruStartMsgFilter(unsigned long ch, unsigned long type, PASSTHRU_MSG *mask,
                                   PASSTHRU_MSG *pattern, PASSTHRU_MSG *flow, unsigned long *pId)
{
    long r;
    NEED(StartFilter);
    logf_("StartMsgFilter ch=%lu type=%lu (1=pass 2=block 3=flow)", ch, type);
    log_msg(" mask", ch, mask);
    log_msg(" pattern", ch, pattern);
    if (flow) log_msg(" flow", ch, flow);
    r = R.StartFilter(ch, type, mask, pattern, flow, pId);
    logf_(" -> ret=%ld id=%lu", r, pId ? *pId : 0);
    return r;
}

long WINAPI PassThruStopMsgFilter(unsigned long ch, unsigned long id)
{
    long r;
    NEED(StopFilter);
    r = R.StopFilter(ch, id);
    logf_("StopMsgFilter ch=%lu id=%lu -> ret=%ld", ch, id, r);
    return r;
}

long WINAPI PassThruSetProgrammingVoltage(unsigned long dev, unsigned long pin, unsigned long mv)
{
    long r;
    NEED(SetProgV);
    r = R.SetProgV(dev, pin, mv);
    logf_("SetProgrammingVoltage dev=%lu pin=%lu mV=%lu -> ret=%ld", dev, pin, mv, r);
    return r;
}

long WINAPI PassThruReadVersion(unsigned long dev, char *fw, char *dll, char *api)
{
    long r;
    NEED(ReadVersion);
    r = R.ReadVersion(dev, fw, dll, api);
    logf_("ReadVersion dev=%lu -> ret=%ld fw=%s dll=%s api=%s", dev, r,
          fw ? fw : "", dll ? dll : "", api ? api : "");
    return r;
}

long WINAPI PassThruGetLastError(char *desc)
{
    init();
    if (g_err[0] && !R.GetLastErr) {
        if (desc) strcpy(desc, g_err);
        return 0;
    }
    if (!R.GetLastErr) return ERR_FAILED;
    return R.GetLastErr(desc);
}

long WINAPI PassThruIoctl(unsigned long ch, unsigned long id, void *in, void *out)
{
    long r;
    unsigned long i;
    char h[1024];
    NEED(Ioctl);
    unsigned long proto = ch < 64 ? g_proto[ch] : 0;
    (void)proto;

    if (id == 2 && in) {                       /* SET_CONFIG */
        SCONFIG_LIST *l = in;
        for (i = 0; l->ConfigPtr && i < l->NumOfParams; i++)
            logf_("Ioctl ch=%lu SET_CONFIG param=0x%lX value=%lu", ch,
                  l->ConfigPtr[i].Parameter, l->ConfigPtr[i].Value);
    } else if (id == 4 && in) {                /* FIVE_BAUD_INIT */
        SBYTE_ARRAY *a = in;
        hex(h, sizeof h, a->BytePtr, a->NumOfBytes > 64 ? 64 : a->NumOfBytes);
        logf_("Ioctl ch=%lu FIVE_BAUD_INIT in=%s", ch, h);
    } else if (id == 5 && in) {                /* FAST_INIT */
        log_msg("Ioctl FAST_INIT in", ch, (PASSTHRU_MSG *)in);
    } else if (id != 1 && id != 3) {
        logf_("Ioctl ch=%lu %s(0x%lX)", ch, ioctl_name(id), id);
    }

    r = R.Ioctl(ch, id, in, out);

    if (id == 1 && in) {                       /* GET_CONFIG: values come back in "in" */
        SCONFIG_LIST *l = in;
        for (i = 0; l->ConfigPtr && i < l->NumOfParams; i++)
            logf_("Ioctl ch=%lu GET_CONFIG param=0x%lX -> %lu", ch,
                  l->ConfigPtr[i].Parameter, l->ConfigPtr[i].Value);
    } else if (id == 3 && out) {
        logf_("Ioctl dev=%lu READ_VBATT -> %lu mV", ch, *(unsigned long *)out);
    } else if (id == 4 && out && r == 0) {
        SBYTE_ARRAY *a = out;
        hex(h, sizeof h, a->BytePtr, a->NumOfBytes > 64 ? 64 : a->NumOfBytes);
        logf_("Ioctl ch=%lu FIVE_BAUD_INIT out=%s", ch, h);
    } else if (id == 5 && out && r == 0) {
        log_msg("Ioctl FAST_INIT out", ch, (PASSTHRU_MSG *)out);
    }
    if (r != 0) logf_("Ioctl ch=%lu %s -> ret=%ld", ch, ioctl_name(id), r);
    return r;
}
