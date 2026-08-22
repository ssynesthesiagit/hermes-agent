import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";
import { Link } from "react-router";
import {
  ArrowUp,
  Bot,
  Check,
  ChevronRight,
  CircleAlert,
  Copy,
  ExternalLink,
  FilePlus2,
  History,
  MessageCircle,
  MoreHorizontal,
  Paperclip,
  Plus,
  Radio,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Wifi,
  WifiOff,
  X,
  Zap,
} from "lucide-react";

import type { GatewayEvent, ConnectionState } from "@/lib/gatewayClient";
import { GatewayClient } from "@/lib/gatewayClient";
import { api } from "@/lib/api";
import {
  canonicalSessionCandidates,
  mergeMobileProfiles,
  validateAvatarDataUrl,
  type GatewayProfileRow,
  type MobileProfileInfo,
} from "@/lib/mobile-bots";
import {
  createEmptyMobileActivityState,
  mergeActivityEvent,
  mergeStreamEvent,
  normalizeHistoryShapes,
  normalizeResumeStream,
  normalizeSessionList,
  type MobileActivityKind,
  type MobileActivityState,
  type MobileActivityItemState,
  toMobileFileUploadRequest,
  type MobileSessionSummary,
  type MobileStreamState,
} from "@/lib/mobile-chat";
import { resolveInitialMobileTab } from "@/lib/root-redirect";
import { copyTextToClipboard } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import { Markdown } from "@/components/Markdown";

import "./MobilePage.css";

const MOBILE_PROFILE_KEY = "hermes.mobile.selected-profile";
const MOBILE_SESSION_KEY = "hermes.mobile.selected-session";
const MOBILE_SESSION_MODE_KEY = "hermes.mobile.session-mode";
const MOBILE_SESSION_PROFILE_KEY = "hermes.mobile.selected-session-profile";
const MOBILE_TAB_KEY = "hermes.mobile.selected-tab";
const CANONICAL_SESSION_TITLE = "Bot Chat";

const MOBILE_TABS = [
  { id: "chat", label: "Chat", icon: MessageCircle },
  { id: "bots", label: "Bots", icon: Bot },
  { id: "sessions", label: "Sessions", icon: History },
  { id: "more", label: "More", icon: MoreHorizontal },
] as const;

const BOT_AVATARS = [
  {
    icon: Sparkles,
    background: "linear-gradient(135deg, #8affdf, #22b8ff)",
    foreground: "#031411",
    glow: "rgba(66, 231, 221, 0.34)",
  },
  {
    icon: Zap,
    background: "linear-gradient(135deg, #d8ff55, #63df57)",
    foreground: "#101600",
    glow: "rgba(177, 255, 78, 0.3)",
  },
  {
    icon: Radio,
    background: "linear-gradient(135deg, #ff79d1, #926dff)",
    foreground: "#180514",
    glow: "rgba(255, 88, 207, 0.32)",
  },
  {
    icon: ShieldCheck,
    background: "linear-gradient(135deg, #ffbf69, #ff5f57)",
    foreground: "#1a0902",
    glow: "rgba(255, 126, 82, 0.3)",
  },
  {
    icon: MessageCircle,
    background: "linear-gradient(135deg, #73a7ff, #5d63ff)",
    foreground: "#05091b",
    glow: "rgba(91, 126, 255, 0.34)",
  },
] as const;

const MOBILE_ACTIVITY_EVENT_TYPES = new Set([
  "message.interim",
  "message.delta",
  "message.complete",
  "thinking.delta",
  "reasoning.delta",
  "reasoning.available",
  "status.update",
  "tool.start",
  "tool.progress",
  "tool.complete",
  "tool.generating",
  "background.complete",
  "error",
]);

type MobileTab = (typeof MOBILE_TABS)[number]["id"];
type MobileRpcResponse = Record<string, unknown>;
type MobilePageProps = { initialTab?: MobileTab };
type MobileSessionMode = "canonical" | "explicit";
type MobileSessionPreference = {
  mode: MobileSessionMode;
  profile: string | null;
  sessionId: string | null;
};

const EMPTY_STREAM: MobileStreamState = {
  messages: [],
  streaming: false,
  error: null,
};

function readPreference(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function writePreference(key: string, value: string | null): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    // Preferences are best-effort in private browsing and locked-down webviews.
  }
}

function readTabPreference(): MobileTab {
  const saved = readPreference(MOBILE_TAB_KEY);
  return MOBILE_TABS.some((tab) => tab.id === saved) ? (saved as MobileTab) : "chat";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : "The mobile console could not complete that request.";
}

function scopedParams(
  params: Record<string, unknown>,
  profile: string,
): Record<string, unknown> {
  return profile ? { ...params, profile } : params;
}

function profileLabel(profile: MobileProfileInfo): string {
  return profile.display_name?.trim() || profile.name;
}

function botMonogram(displayName: string): string {
  const words = displayName.trim().split(/\s+/).filter(Boolean);
  const monogram = words
    .slice(0, 2)
    .map((word) => Array.from(word)[0] ?? "")
    .join("")
    .toUpperCase();
  return monogram || "B";
}

function botAvatar(profileName: string) {
  let hash = 2166136261;
  for (const character of profileName) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  return BOT_AVATARS[hash % BOT_AVATARS.length];
}

function normalizeGatewayProfiles(response: unknown): GatewayProfileRow[] {
  const record = asRecord(response);
  if (!Array.isArray(record?.profiles)) return [];

  return record.profiles.flatMap((value) => {
    const profile = asRecord(value);
    const name = stringValue(profile?.name);
    return name ? [{ ...profile, name } as GatewayProfileRow] : [];
  });
}

function readMobileSessionPreference(selectedProfile: string): MobileSessionPreference {
  const mode: MobileSessionMode =
    readPreference(MOBILE_SESSION_MODE_KEY) === "explicit" ? "explicit" : "canonical";
  const profile = readPreference(MOBILE_SESSION_PROFILE_KEY);
  const sessionId = readPreference(MOBILE_SESSION_KEY);
  if (mode !== "explicit" || !sessionId || !profile || profile !== selectedProfile) {
    return { mode: "canonical", profile: null, sessionId: null };
  }
  return { mode, profile, sessionId };
}

function formatSessionDate(timestamp: number): string {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return "No activity";
  const date = new Date(timestamp > 10_000_000_000 ? timestamp : timestamp * 1000);
  if (Number.isNaN(date.getTime())) return "No activity";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
  }).format(date);
}

function activityKindLabel(kind: MobileActivityKind): string {
  switch (kind) {
    case "reasoning":
      return "MODEL";
    case "tool":
      return "TOOL";
    case "status":
      return "STATUS";
    case "background":
      return "BACKGROUND";
  }
}

function activityStateLabel(state: MobileActivityItemState): string {
  return state.toUpperCase();
}

function formatActivityDuration(seconds: number): string {
  if (seconds < 1) return "<1s";
  return seconds < 10 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds)}s`;
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      if (typeof reader.result === "string") resolve(reader.result);
      else reject(new Error(`Could not read ${file.name}.`));
    });
    reader.addEventListener("error", () => {
      reject(new Error(`Could not read ${file.name}.`));
    });
    reader.readAsDataURL(file);
  });
}

function mobileAttachmentText(
  response: MobileRpcResponse,
  filename: string,
  image: boolean,
): string | null {
  if (image) return null;
  return (
    stringValue(response.ref_text) ??
    `[attached file: ${filename}]`
  );
}

export default function MobilePage({ initialTab }: MobilePageProps = {}) {
  const gateway = useMemo(() => new GatewayClient(), []);
  const [activeTab, setActiveTab] = useState<MobileTab>(() =>
    resolveInitialMobileTab(readTabPreference(), initialTab),
  );
  const [connectionState, setConnectionState] = useState<ConnectionState>("idle");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<MobileProfileInfo[]>([]);
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profilesReady, setProfilesReady] = useState(false);
  const [gatewayProfilesReady, setGatewayProfilesReady] = useState(false);
  const [activeProfileName, setActiveProfileName] = useState("");
  const [selectedProfile, setSelectedProfile] = useState(() =>
    readPreference(MOBILE_PROFILE_KEY),
  );
  const [initialSessionPreference] = useState(() =>
    readMobileSessionPreference(readPreference(MOBILE_PROFILE_KEY)),
  );
  const [sessionMode, setSessionMode] = useState<MobileSessionMode>(
    initialSessionPreference.mode,
  );
  const [sessions, setSessions] = useState<MobileSessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [durableSessionId, setDurableSessionId] = useState<string | null>(
    initialSessionPreference.sessionId,
  );
  const [avatarDataUrls, setAvatarDataUrls] = useState<Record<string, string>>({});
  const [runtimeSessionId, setRuntimeSessionId] = useState<string | null>(null);
  const [stream, setStream] = useState<MobileStreamState>(EMPTY_STREAM);
  const [activity, setActivity] = useState<MobileActivityState>(() =>
    createEmptyMobileActivityState(),
  );
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);

  const selectedProfileRef = useRef(selectedProfile);
  const profilesRef = useRef<MobileProfileInfo[]>([]);
  const sessionModeRef = useRef<MobileSessionMode>(sessionMode);
  const sessionProfileRef = useRef<string | null>(initialSessionPreference.profile);
  const forcedInitialTabRef = useRef(initialTab);
  const runtimeSessionRef = useRef<string | null>(null);
  const durableSessionRef = useRef<string | null>(durableSessionId);
  const activeTabRef = useRef(activeTab);
  const syncPromiseRef = useRef<Promise<void> | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sendGenerationRef = useRef(0);
  const activeSendRef = useRef<{
    generation: number;
    runtimeId: string;
    submitted: boolean;
  } | null>(null);
  const stoppingRuntimeRef = useRef<string | null>(null);
  const streamRevisionRef = useRef(0);
  const avatarRefreshGenerationRef = useRef(0);
  const copyFeedbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleCopyResponse = useCallback(async (messageId: string, text: string) => {
    if (!text.trim()) return;
    if (!(await copyTextToClipboard(text))) return;

    setCopiedMessageId(messageId);
    if (copyFeedbackTimerRef.current) clearTimeout(copyFeedbackTimerRef.current);
    copyFeedbackTimerRef.current = setTimeout(() => {
      setCopiedMessageId((current) => (current === messageId ? null : current));
      copyFeedbackTimerRef.current = null;
    }, 1500);
  }, []);

  useEffect(
    () => () => {
      if (copyFeedbackTimerRef.current) clearTimeout(copyFeedbackTimerRef.current);
    },
    [],
  );

  useEffect(() => {
    selectedProfileRef.current = selectedProfile;
    if (
      sessionModeRef.current === "explicit" &&
      sessionProfileRef.current !== selectedProfile
    ) {
      sessionModeRef.current = "canonical";
      sessionProfileRef.current = null;
      durableSessionRef.current = null;
      setSessionMode("canonical");
      setDurableSessionId(null);
    }
    writePreference(MOBILE_PROFILE_KEY, selectedProfile || null);
  }, [selectedProfile]);

  useEffect(() => {
    activeTabRef.current = activeTab;
    const isForcedInitialTab = forcedInitialTabRef.current !== undefined;
    forcedInitialTabRef.current = undefined;
    if (isForcedInitialTab) return;
    writePreference(MOBILE_TAB_KEY, activeTab);
  }, [activeTab]);

  useEffect(() => {
    sessionModeRef.current = sessionMode;
    durableSessionRef.current = durableSessionId;
    const persistExplicitSession =
      sessionMode === "explicit" &&
      Boolean(durableSessionId) &&
      Boolean(selectedProfile) &&
      sessionProfileRef.current === selectedProfile;
    writePreference(MOBILE_SESSION_MODE_KEY, sessionMode);
    writePreference(
      MOBILE_SESSION_KEY,
      persistExplicitSession ? durableSessionId : null,
    );
    writePreference(
      MOBILE_SESSION_PROFILE_KEY,
      persistExplicitSession ? selectedProfile : null,
    );
  }, [durableSessionId, selectedProfile, sessionMode]);

  const loadProfiles = useCallback(async () => {
    setProfilesLoading(true);
    setProfileError(null);

    const [profilesResult, activeResult] = await Promise.allSettled([
      api.getProfiles(),
      api.getActiveProfile(),
    ]);

    const profileRows =
      profilesResult.status === "fulfilled" ? profilesResult.value.profiles : [];
    const active =
      activeResult.status === "fulfilled"
        ? activeResult.value.active || activeResult.value.current
        : "";
    const preferred = readPreference(MOBILE_PROFILE_KEY);
    const names = new Set(profileRows.map((profile) => profile.name));
    const candidate =
      (preferred && (names.size === 0 || names.has(preferred)) && preferred) ||
      (active && (names.size === 0 || names.has(active)) && active) ||
      profileRows[0]?.name ||
      preferred;

    const profileNames = new Set(profileRows.map((profile) => profile.name));
    const mergedProfiles =
      profilesResult.status === "fulfilled"
        ? mergeMobileProfiles(profileRows, profilesRef.current).filter((profile) =>
            profileNames.has(profile.name),
          )
        : profilesRef.current;
    profilesRef.current = mergedProfiles;
    setProfiles(mergedProfiles);
    setActiveProfileName(active);
    if (candidate && candidate !== selectedProfileRef.current) {
      selectedProfileRef.current = candidate;
      setSelectedProfile(candidate);
    }

    if (
      profilesResult.status === "rejected" &&
      activeResult.status === "rejected"
    ) {
      setProfileError(errorMessage(profilesResult.reason));
    } else if (profilesResult.status === "rejected") {
      setProfileError(errorMessage(profilesResult.reason));
    }

    setProfilesReady(true);
    setProfilesLoading(false);
  }, []);

  useEffect(() => {
    void loadProfiles();
  }, [loadProfiles]);

  const refreshGatewayProfiles = useCallback(async () => {
    if (gateway.connectionState !== "open") return;
    const generation = avatarRefreshGenerationRef.current + 1;
    avatarRefreshGenerationRef.current = generation;

    try {
      const response = await gateway.request<MobileRpcResponse>("profiles.list", {
        include_sessions: false,
      });
      const gatewayRows = normalizeGatewayProfiles(response);
      if (generation !== avatarRefreshGenerationRef.current) return;
      if (gatewayRows.length === 0) {
        setGatewayProfilesReady(true);
        return;
      }

      const gatewayNames = new Set(gatewayRows.map((profile) => profile.name));
      const mergedProfiles = mergeMobileProfiles(profilesRef.current, gatewayRows).filter(
        (profile) => gatewayNames.has(profile.name),
      );
      profilesRef.current = mergedProfiles;
      setProfiles(mergedProfiles);

      const avatarResults = await Promise.all(
        gatewayRows
          .filter((profile) => profile.has_avatar === true)
          .map(async (profile) => {
            try {
              const asset = await gateway.request<MobileRpcResponse>(
                "profiles.get_asset",
                { name: profile.name, asset: "avatar" },
              );
              return [profile.name, validateAvatarDataUrl(asset)] as const;
            } catch {
              return [profile.name, null] as const;
            }
          }),
      );
      if (generation !== avatarRefreshGenerationRef.current) return;

      setAvatarDataUrls((previous) => {
        const next = { ...previous };
        for (const profile of gatewayRows) {
          if (profile.has_avatar !== true) delete next[profile.name];
        }
        for (const [name, data] of avatarResults) {
          if (data) next[name] = data;
          else delete next[name];
        }
        return next;
      });
      setGatewayProfilesReady(true);
    } catch {
      // REST profile loading remains the compatibility path for older gateways.
      if (generation === avatarRefreshGenerationRef.current) {
        setGatewayProfilesReady(true);
      }
    }
  }, [gateway]);

  useEffect(() => {
    if (connectionState !== "open") return;

    void refreshGatewayProfiles();
    return () => {
      avatarRefreshGenerationRef.current += 1;
    };
  }, [connectionState, refreshGatewayProfiles]);

  const invalidateAsyncSend = useCallback(() => {
    sendGenerationRef.current += 1;
    activeSendRef.current = null;
    setSending(false);
  }, []);

  const bindSession = useCallback(
    (runtimeId: string, durableId: string | null, response: unknown) => {
      const previousRuntimeId = runtimeSessionRef.current;
      if (previousRuntimeId && previousRuntimeId !== runtimeId) {
        invalidateAsyncSend();
      }
      runtimeSessionRef.current = runtimeId;
      durableSessionRef.current = durableId;
      setRuntimeSessionId(runtimeId);
      setDurableSessionId(durableId);
      stoppingRuntimeRef.current = null;
      setStopping(false);
      streamRevisionRef.current += 1;
      setStream(normalizeResumeStream(response));
      setActivity(createEmptyMobileActivityState());
      setSessionError(null);
      setNotice(null);
    },
    [invalidateAsyncSend],
  );

  const hydrateSession = useCallback(
    async (runtimeId: string, fallbackResponse: unknown) => {
      const revisionAtStart = streamRevisionRef.current;
      const fallback = normalizeResumeStream(fallbackResponse);
      try {
        const response = await gateway.request<MobileRpcResponse>(
          "session.history",
          { session_id: runtimeId },
        );
        if (runtimeSessionRef.current !== runtimeId) return;
        const history = normalizeHistoryShapes(response);
        const hydrated = normalizeResumeStream(
          history.length > 0 ? response : fallbackResponse,
          fallbackResponse,
        );
        setStream((previous) => {
          if (
            runtimeSessionRef.current !== runtimeId ||
            streamRevisionRef.current !== revisionAtStart
          ) {
            return previous;
          }
          return hydrated;
        });
      } catch (error) {
        if (runtimeSessionRef.current !== runtimeId) return;
        setStream((previous) =>
          streamRevisionRef.current === revisionAtStart ? fallback : previous,
        );
        setSessionError(`History unavailable: ${errorMessage(error)}`);
      }
    },
    [gateway],
  );

  const createSession = useCallback(
    async (
      profile = selectedProfileRef.current,
      mode: MobileSessionMode = sessionModeRef.current,
    ) => {
      if (mode === "explicit") sessionProfileRef.current = profile;
      const response = await gateway.request<MobileRpcResponse>(
        "session.create",
        scopedParams(
          {
            source: "mobile",
            ...(mode === "canonical"
              ? { title: CANONICAL_SESSION_TITLE, hidden: true }
              : {}),
          },
          profile,
        ),
      );
      const runtimeId = stringValue(response.session_id);
      if (!runtimeId) throw new Error("The gateway did not return a session id.");
      const durableId = stringValue(response.stored_session_id);
      bindSession(runtimeId, durableId, response);
      await hydrateSession(runtimeId, response);
    },
    [bindSession, gateway, hydrateSession],
  );

  const resumeSession = useCallback(
    async (durableId: string, profile = selectedProfileRef.current) => {
      const response = await gateway.request<MobileRpcResponse>(
        "session.resume",
        scopedParams(
          { session_id: durableId, source: "mobile" },
          profile,
        ),
      );
      const runtimeId = stringValue(response.session_id);
      if (!runtimeId) throw new Error("The gateway did not return a runtime session id.");
      bindSession(runtimeId, stringValue(response.resumed) ?? durableId, response);
      await hydrateSession(runtimeId, response);
    },
    [bindSession, gateway, hydrateSession],
  );

  const refreshSessions = useCallback(async () => {
    if (gateway.connectionState !== "open") return [];
    const profile = selectedProfileRef.current;
    setSessionsLoading(true);
    try {
      const response = await gateway.request<MobileRpcResponse>(
        "session.list",
        scopedParams({ limit: 80 }, profile),
      );
      const rows = normalizeSessionList(response);
      if (profile === selectedProfileRef.current) setSessions(rows);
      return rows;
    } catch (error) {
      if (profile === selectedProfileRef.current) {
        setSessionError(`Sessions unavailable: ${errorMessage(error)}`);
      }
      return [];
    } finally {
      setSessionsLoading(false);
    }
  }, [gateway]);

  const syncSession = useCallback(async () => {
    if (gateway.connectionState !== "open") return;
    if (syncPromiseRef.current) return syncPromiseRef.current;

    const promise = (async () => {
      setSessionBusy(true);
      const profile = selectedProfileRef.current;
      try {
        if (
          sessionModeRef.current === "explicit" &&
          sessionProfileRef.current === profile
        ) {
          const preferred = durableSessionRef.current;
          if (preferred) {
            try {
              await resumeSession(preferred, profile);
              return;
            } catch {
              durableSessionRef.current = null;
              setDurableSessionId(null);
            }
          }
          await createSession(profile, "explicit");
          return;
        }

        for (const candidate of canonicalSessionCandidates(
          profilesRef.current.find((item) => item.name === profile),
        )) {
          try {
            await resumeSession(candidate, profile);
            return;
          } catch {
            // The pinned chat may have been removed; try the shared title next.
          }
        }
        await createSession(profile, "canonical");
      } catch (error) {
        setSessionError(errorMessage(error));
      } finally {
        setSessionBusy(false);
        syncPromiseRef.current = null;
      }
    })();

    syncPromiseRef.current = promise;
    return promise;
  }, [createSession, gateway, resumeSession]);

  useEffect(() => {
    if (!profilesReady || !gatewayProfilesReady || connectionState !== "open") return;
    void syncSession();
  }, [
    connectionState,
    gatewayProfilesReady,
    profilesReady,
    selectedProfile,
    syncSession,
  ]);

  useEffect(() => {
    if (activeTab === "sessions" && connectionState === "open") {
      void refreshSessions();
    }
  }, [activeTab, connectionState, refreshSessions]);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let retryDelay = 800;

    const scheduleReconnect = () => {
      if (disposed || retryTimer || navigator.onLine === false) return;
      const wait = retryDelay;
      retryDelay = Math.min(retryDelay * 2, 12_000);
      retryTimer = setTimeout(() => {
        retryTimer = undefined;
        void connectNow();
      }, wait);
    };

    const connectNow = async () => {
      if (disposed || navigator.onLine === false) return;
      if (
        gateway.connectionState === "open" ||
        gateway.connectionState === "connecting"
      ) {
        return;
      }
      try {
        await gateway.connect();
        retryDelay = 800;
        setConnectionError(null);
      } catch (error) {
        if (!disposed) {
          setConnectionError(errorMessage(error));
          scheduleReconnect();
        }
      }
    };

    const offState = gateway.onState((state) => {
      setConnectionState(state);
      if (state === "open") {
        retryDelay = 800;
        setConnectionError(null);
      } else if (state === "closed" || state === "error") {
        avatarRefreshGenerationRef.current += 1;
        setGatewayProfilesReady(false);
        invalidateAsyncSend();
        stoppingRuntimeRef.current = null;
        setStopping(false);
        scheduleReconnect();
      }
    });

    const offEvents = gateway.onAny((event: GatewayEvent) => {
      if (!MOBILE_ACTIVITY_EVENT_TYPES.has(event.type)) return;
      if (!runtimeSessionRef.current || event.session_id !== runtimeSessionRef.current) {
        return;
      }

      const runtimeId = runtimeSessionRef.current;
      const transcriptEvent =
        event.type === "message.interim" ||
        event.type === "message.delta" ||
        event.type === "message.complete" ||
        event.type === "error";
      const terminalEvent =
        event.type === "message.complete" || event.type === "error";
      const wasStopping = stoppingRuntimeRef.current === runtimeId;
      if (transcriptEvent) {
        streamRevisionRef.current += 1;
        setStream((previous) => {
          const next = mergeStreamEvent(previous, event, runtimeId);
          return terminalEvent
            ? {
                ...next,
                messages: next.messages.map((message) =>
                  message.optimistic ? { ...message, optimistic: false } : message,
                ),
              }
            : next;
        });
      }
      setActivity((previous) => mergeActivityEvent(previous, event, runtimeId));
      if (event.type === "message.delta" || event.type === "message.interim") {
        setSessionError(null);
        setNotice(null);
      } else if (event.type === "error") {
        const payload = asRecord(event.payload);
        setSessionError(stringValue(payload?.message) ?? "The gateway returned an error.");
      } else if (event.type === "message.complete") {
        setNotice(null);
        if (activeTabRef.current === "sessions") void refreshSessions();
      }
      if (event.type === "message.complete" || event.type === "error") {
        if (stoppingRuntimeRef.current === runtimeId) {
          stoppingRuntimeRef.current = null;
          setStopping(false);
          if (wasStopping && event.type === "message.complete") {
            setNotice("Turn interrupted.");
          }
        }
        if (activeSendRef.current?.runtimeId === runtimeId) {
          activeSendRef.current = null;
          setSending(false);
        }
      }
    });

    const onOnline = () => {
      retryDelay = 200;
      void connectNow();
    };
    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible") return;
      if (gateway.connectionState !== "open") void connectNow();
      else if (!runtimeSessionRef.current) void syncSession();
    };

    window.addEventListener("online", onOnline);
    document.addEventListener("visibilitychange", onVisibilityChange);
    void connectNow();

    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      offState();
      offEvents();
      gateway.close();
      window.removeEventListener("online", onOnline);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [gateway, invalidateAsyncSend, refreshSessions, syncSession]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      transcriptEndRef.current?.scrollIntoView({
        behavior: reducedMotion ? "auto" : "smooth",
        block: "end",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [reducedMotion, stream.messages, stream.streaming]);

  const handleProfileSelect = useCallback(
    (name: string) => {
      if (sessionBusy || stream.streaming || sending || stopping) return;
      if (name === selectedProfileRef.current) {
        setActiveTab("chat");
        if (sessionModeRef.current === "canonical") return;
      }
      const selectedSameProfile = name === selectedProfileRef.current;
      selectedProfileRef.current = name;
      invalidateAsyncSend();
      stoppingRuntimeRef.current = null;
      setStopping(false);
      streamRevisionRef.current += 1;
      runtimeSessionRef.current = null;
      sessionModeRef.current = "canonical";
      sessionProfileRef.current = null;
      setSessionMode("canonical");
      durableSessionRef.current = null;
      setSelectedProfile(name);
      setRuntimeSessionId(null);
      setDurableSessionId(null);
      setStream(EMPTY_STREAM);
      setActivity(createEmptyMobileActivityState());
      setSessionError(null);
      setNotice(`Starting a ${name} console…`);
      setActiveTab("chat");
      if (selectedSameProfile) void syncSession();
    },
    [invalidateAsyncSend, sending, sessionBusy, stopping, stream.streaming, syncSession],
  );

  const handleNewSession = useCallback(async () => {
    if (stream.streaming || stopping || connectionState !== "open") return;
    invalidateAsyncSend();
    stoppingRuntimeRef.current = null;
    setStopping(false);
    streamRevisionRef.current += 1;
    runtimeSessionRef.current = null;
    sessionModeRef.current = "explicit";
    sessionProfileRef.current = selectedProfileRef.current;
    setSessionMode("explicit");
    durableSessionRef.current = null;
    setRuntimeSessionId(null);
    setDurableSessionId(null);
    setStream(EMPTY_STREAM);
    setActivity(createEmptyMobileActivityState());
    setSessionError(null);
    setSessionBusy(true);
    try {
      await createSession(selectedProfileRef.current, "explicit");
    } catch (error) {
      setSessionError(errorMessage(error));
    } finally {
      setSessionBusy(false);
    }
  }, [connectionState, createSession, invalidateAsyncSend, stopping, stream.streaming]);

  const handleResume = useCallback(
    async (id: string) => {
      if (stream.streaming || connectionState !== "open") return;
      setActiveTab("chat");
      sessionModeRef.current = "explicit";
      sessionProfileRef.current = selectedProfileRef.current;
      setSessionMode("explicit");
      durableSessionRef.current = id;
      setDurableSessionId(id);
      setSessionBusy(true);
      setSessionError(null);
      try {
        await resumeSession(id);
      } catch (error) {
        setSessionError(errorMessage(error));
      } finally {
        setSessionBusy(false);
      }
    },
    [connectionState, resumeSession, stream.streaming],
  );

  const handleStop = useCallback(async () => {
    if (stopping) return;
    const runtimeId = runtimeSessionRef.current;
    const activeSend = activeSendRef.current;
    const hasPreSubmitSend = Boolean(
      activeSend && activeSend.runtimeId === runtimeId && !activeSend.submitted,
    );

    if (hasPreSubmitSend || (!runtimeId && sending)) {
      invalidateAsyncSend();
      stoppingRuntimeRef.current = null;
      setStopping(false);
      streamRevisionRef.current += 1;
      setStream((previous) => ({
        ...previous,
        streaming: false,
        error: null,
        messages: previous.messages.filter((message) => !message.optimistic),
      }));
      setNotice("Message cancelled.");
      return;
    }

    if (!runtimeId || connectionState !== "open") return;

    invalidateAsyncSend();
    stoppingRuntimeRef.current = runtimeId;
    setStopping(true);
    try {
      await gateway.request("session.interrupt", { session_id: runtimeId });
    } catch (error) {
      if (stoppingRuntimeRef.current === runtimeId) {
        stoppingRuntimeRef.current = null;
        setStopping(false);
      }
      setSessionError(errorMessage(error));
    }
  }, [
    connectionState,
    gateway,
    invalidateAsyncSend,
    sending,
    stopping,
  ]);

  const sendMessage = useCallback(async () => {
    const text = draft.trim();
    const file = pendingFile;
    if ((!text && !file) || sending || stopping || stream.streaming) return;
    if (connectionState !== "open") {
      setSessionError("Connect to the gateway before sending a message.");
      return;
    }

    const generation = sendGenerationRef.current + 1;
    sendGenerationRef.current = generation;
    setSending(true);
    setSessionError(null);
    setNotice(null);
    setActivity(createEmptyMobileActivityState());

    try {
      if (!runtimeSessionRef.current) await createSession();
      if (sendGenerationRef.current !== generation) return;
      const runtimeId = runtimeSessionRef.current;
      if (!runtimeId) throw new Error("No active mobile session.");

      activeSendRef.current = {
        generation,
        runtimeId,
        submitted: false,
      };
      const isCurrentSend = () =>
        sendGenerationRef.current === generation &&
        runtimeSessionRef.current === runtimeId &&
        gateway.connectionState === "open" &&
        activeSendRef.current?.generation === generation &&
        activeSendRef.current.runtimeId === runtimeId;

      const optimisticId = `mobile-user-${Date.now()}-${Math.random()
        .toString(36)
        .slice(2, 8)}`;
      const displayText = [
        text,
        file ? `[attachment: ${file.name}]` : "",
      ]
        .filter(Boolean)
        .join("\n\n");
      setStream((previous) => ({
        ...previous,
        streaming: true,
        error: null,
        messages: [
          ...previous.messages,
          {
            id: optimisticId,
            role: "user",
            text: displayText,
            optimistic: true,
          },
        ],
      }));
      setDraft("");

      let submitText = text;
      if (file) {
        const dataUrl = await readFileAsDataUrl(file);
        if (!isCurrentSend()) return;
        const upload = toMobileFileUploadRequest({
          sessionId: runtimeId,
          file,
          dataUrl,
        });
        const uploadResponse = await gateway.request<MobileRpcResponse>(
          upload.method,
          upload.params,
        );
        if (!isCurrentSend()) return;
        const attachmentText = mobileAttachmentText(
          uploadResponse,
          file.name,
          upload.method === "image.attach_bytes",
        );
        if (attachmentText) {
          submitText = [attachmentText, submitText].filter(Boolean).join("\n\n");
        } else if (!submitText) {
          submitText = "What do you see in this image?";
        }
        if (!isCurrentSend()) return;
        setPendingFile(null);
      }

      if (!submitText) throw new Error("Add a message or attachment first.");
      if (!isCurrentSend()) return;
      activeSendRef.current.submitted = true;
      await gateway.request("prompt.submit", {
        session_id: runtimeId,
        text: submitText,
      });
      if (!isCurrentSend()) return;
      setStream((previous) => ({
        ...previous,
        streaming: true,
        messages: previous.messages.map((message) =>
          message.id === optimisticId
            ? { ...message, optimistic: false }
            : message,
        ),
      }));
    } catch (error) {
      if (
        sendGenerationRef.current !== generation ||
        activeSendRef.current?.generation !== generation
      ) {
        return;
      }
      const message = errorMessage(error);
      setSessionError(message);
      setStream((previous) => ({
        ...previous,
        streaming: false,
        error: message,
        messages: previous.messages.map((item) =>
          item.optimistic ? { ...item, optimistic: false, error: true } : item,
        ),
      }));
      activeSendRef.current = null;
    } finally {
      if (sendGenerationRef.current === generation) setSending(false);
    }
  }, [
    connectionState,
    createSession,
    draft,
    gateway,
    pendingFile,
    sending,
    stopping,
    stream.streaming,
  ]);

  const handleFileChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0] ?? null;
      event.target.value = "";
      if (file && !stopping) {
        setPendingFile(file);
        setSessionError(null);
        setNotice(`${file.name} will upload with your next message.`);
      }
    },
    [stopping],
  );

  const forceReconnect = useCallback(() => {
    gateway.close();
    void gateway.connect().catch(() => {});
  }, [gateway]);

  const handleAvatarError = useCallback((name: string) => {
    setAvatarDataUrls((previous) => {
      if (!previous[name]) return previous;
      const next = { ...previous };
      delete next[name];
      return next;
    });
  }, []);

  const selectedProfileInfo = profiles.find(
    (profile) => profile.name === selectedProfile,
  );
  const selectedBotName = selectedProfileInfo
    ? profileLabel(selectedProfileInfo)
    : selectedProfile || "Hermes";
  const selectedBotDescription =
    selectedProfileInfo?.description?.trim() ||
    selectedProfileInfo?.model?.trim() ||
    "Bot-scoped Hermes agent";
  const selectedBotProvider = selectedProfileInfo?.provider?.trim() || "provider configured for bot";
  const online = connectionState === "open";
  const statusText = !online
    ? connectionState === "connecting"
      ? "Connecting…"
      : "Offline"
    : stopping
      ? "Stopping…"
    : sending
      ? "Uploading…"
      : stream.streaming
        ? "Thinking…"
        : sessionBusy
          ? "Syncing…"
          : "Ready";

  const submitDisabled =
    !online || sending || stopping || stream.streaming || sessionBusy || (!draft.trim() && !pendingFile);
  const activityIsLive =
    stream.streaming ||
    sending ||
    stopping ||
    activity.items.some((item) => item.state === "running");

  return (
    <div className="mobile-console" data-connection={online ? "online" : "offline"}>
      <header className="mobile-console__header">
        <div className="mobile-console__brand">
          <div className="mobile-console__brand-mark" aria-hidden="true">
            <Zap />
          </div>
          <div>
            <p className="mobile-console__eyebrow">HERMES // REMOTE</p>
            <h1>{MOBILE_TABS.find((tab) => tab.id === activeTab)?.label}</h1>
          </div>
        </div>
        <button
          type="button"
          className="mobile-console__connection"
          onClick={forceReconnect}
          aria-label={online ? "Reconnect to gateway" : "Connect to gateway"}
        >
          {online ? <Wifi aria-hidden="true" /> : <WifiOff aria-hidden="true" />}
          <span className="mobile-console__connection-dot" aria-hidden="true" />
          <span>{statusText}</span>
        </button>
      </header>

      <main className="mobile-console__main">
        <div className="mobile-console__content">
          {(connectionError || sessionError || stream.error) && (
            <div className="mobile-console__alert" role="alert">
              <CircleAlert aria-hidden="true" />
              <span>{connectionError || sessionError || stream.error}</span>
              <button
                type="button"
                className="mobile-console__icon-button"
                onClick={() => {
                  setConnectionError(null);
                  setSessionError(null);
                  setStream((previous) => ({ ...previous, error: null }));
                }}
                aria-label="Dismiss error"
              >
                <X aria-hidden="true" />
              </button>
            </div>
          )}

          {notice && !connectionError && !sessionError && !stream.error && (
            <p className="mobile-console__notice" role="status">
              {notice}
            </p>
          )}

          {activeTab === "chat" && (
            <section className="mobile-console__panel mobile-console__chat-panel" aria-labelledby="mobile-chat-title">
              <div className="mobile-console__panel-heading">
                <div>
                  <p className="mobile-console__eyebrow">DIRECT BOT CHAT</p>
                  <h2 id="mobile-chat-title">{selectedBotName}</h2>
                  <p className="mobile-console__chat-context">
                    {selectedBotDescription} <span aria-hidden="true">·</span> {selectedBotProvider}
                  </p>
                </div>
                <div className="mobile-console__heading-actions">
                  <button
                    type="button"
                    className="mobile-console__small-button"
                    onClick={() => setActiveTab("bots")}
                  >
                    <Bot aria-hidden="true" />
                    <span>Change bot</span>
                  </button>
                  <button
                    type="button"
                    className="mobile-console__small-button"
                    onClick={() => void handleNewSession()}
                    disabled={!online || sessionBusy || stream.streaming}
                  >
                    <Plus aria-hidden="true" />
                    <span>New</span>
                  </button>
                </div>
              </div>

              {(stream.streaming || sending || stopping) && (
                <div className="mobile-console__activity" role="status" aria-live="polite">
                  <span className="mobile-console__activity-pulse" aria-hidden="true" />
                  <strong>
                    {stopping ? "Stopping…" : sending ? "Sending…" : "Thinking…"}
                  </strong>
                  <span>{selectedBotName} is working on this turn</span>
                </div>
              )}

              {activity.items.length > 0 && (
                <section
                  className="mobile-console__activity-log"
                  aria-labelledby="mobile-activity-title"
                >
                  <div className="mobile-console__activity-log-heading">
                    <div className="mobile-console__activity-log-title">
                      <span
                        className={cn(
                          "mobile-console__activity-beacon",
                          activityIsLive && "mobile-console__activity-beacon--live",
                        )}
                        aria-hidden="true"
                      />
                      <div>
                        <p className="mobile-console__eyebrow">BOT ACTIVITY</p>
                        <h3 id="mobile-activity-title">
                          {activityIsLive ? "LIVE TURN" : "TURN COMPLETE"}
                        </h3>
                      </div>
                    </div>
                    <span className="mobile-console__activity-count">
                      {activity.items.length} EVENTS
                    </span>
                  </div>
                  <ol
                    className="mobile-console__activity-list"
                    role="log"
                    aria-live="polite"
                    aria-label="Bot activity feed"
                  >
                    {activity.items.map((item) => (
                      <li
                        className="mobile-console__activity-item"
                        data-kind={item.kind}
                        data-state={item.state}
                        key={item.id}
                      >
                        <div className="mobile-console__activity-item-heading">
                          <span className="mobile-console__activity-kind">
                            {activityKindLabel(item.kind)}
                          </span>
                          <strong>{item.label}</strong>
                          <span className="mobile-console__activity-state">
                            {activityStateLabel(item.state)}
                          </span>
                        </div>
                        <p>{item.text}</p>
                        {item.durationSeconds !== undefined && (
                          <span className="mobile-console__activity-duration">
                            {formatActivityDuration(item.durationSeconds)}
                          </span>
                        )}
                      </li>
                    ))}
                  </ol>
                </section>
              )}

              <div className="mobile-console__transcript" aria-live="polite">
                {stream.messages.length === 0 ? (
                  <div className="mobile-console__empty">
                    <div className="mobile-console__empty-icon" aria-hidden="true">
                      <Radio />
                    </div>
                    <h3>Ready when you are.</h3>
                    <p>
                      This phone is a remote console for your Hermes gateway. Pick a bot,
                      then send a message from anywhere.
                    </p>
                    {!runtimeSessionId && (
                      <button
                        type="button"
                        className="mobile-console__text-button"
                        onClick={() => setActiveTab("bots")}
                      >
                        Choose a bot <ChevronRight aria-hidden="true" />
                      </button>
                    )}
                  </div>
                ) : (
                  stream.messages.map((message, index) => (
                    <article
                      className={cn(
                        "mobile-message",
                        `mobile-message--${message.role}`,
                        message.error && "mobile-message--error",
                      )}
                      key={`${message.id}-${index}`}
                    >
                      <div className="mobile-message__meta">
                        <span>{message.role === "user" ? "YOU" : message.role.toUpperCase()}</span>
                        {message.optimistic && <span className="mobile-message__pending">SENDING</span>}
                      </div>
                      {message.role === "assistant" && !message.error ? (
                        <Markdown
                          content={message.text || (message.streaming ? "" : "No response text.")}
                        />
                      ) : (
                        <p>{message.text || (message.streaming ? "" : "No response text.")}</p>
                      )}
                      {message.streaming && (
                        <span className="mobile-message__cursor" aria-label="Streaming response">
                          ▍
                        </span>
                      )}
                      {message.role === "assistant" &&
                        !message.error &&
                        !message.streaming &&
                        Boolean(message.text.trim()) && (
                          <div className="mobile-message__actions">
                            <button
                              type="button"
                              className="mobile-message__copy"
                              onClick={() => void handleCopyResponse(message.id, message.text)}
                              aria-label="Copy response"
                              title="Copy response"
                            >
                              <Copy aria-hidden="true" />
                            </button>
                            {copiedMessageId === message.id && (
                              <span
                                className="mobile-message__copy-feedback"
                                role="status"
                                aria-live="polite"
                              >
                                Copied
                              </span>
                            )}
                          </div>
                        )}
                    </article>
                  ))
                )}
                <div ref={transcriptEndRef} aria-hidden="true" />
              </div>

              <form
                className="mobile-console__composer"
                onSubmit={(event: FormEvent<HTMLFormElement>) => {
                  event.preventDefault();
                  void sendMessage();
                }}
              >
                {pendingFile && (
                  <div className="mobile-console__attachment-chip">
                    <FilePlus2 aria-hidden="true" />
                    <span title={pendingFile.name}>{pendingFile.name}</span>
                    <button
                      type="button"
                      onClick={() => setPendingFile(null)}
                      disabled={stopping}
                      aria-label={`Remove ${pendingFile.name}`}
                    >
                      <X aria-hidden="true" />
                    </button>
                  </div>
                )}
                <div className="mobile-console__composer-box">
                  <textarea
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder={`Message ${selectedBotName}…`}
                    aria-label={`Message ${selectedBotName}`}
                    rows={2}
                    disabled={!online || sessionBusy || stopping}
                  />
                  <div className="mobile-console__composer-actions">
                    <input
                      ref={fileInputRef}
                      type="file"
                      className="mobile-console__hidden-input"
                      accept="image/*,.pdf,.txt,.md,.json,.csv"
                      onChange={handleFileChange}
                      aria-label="Choose a file or photo"
                    />
                    <button
                      type="button"
                      className="mobile-console__icon-button"
                      onClick={() => fileInputRef.current?.click()}
                      disabled={!online || sessionBusy || sending || stopping}
                      aria-label="Attach a file or photo"
                    >
                      <Paperclip aria-hidden="true" />
                    </button>
                    {stream.streaming || sending || stopping ? (
                      <button
                        type="button"
                        className="mobile-console__send-button mobile-console__send-button--stop"
                        onClick={() => void handleStop()}
                        disabled={stopping}
                        aria-label="Stop response"
                      >
                        <Square aria-hidden="true" />
                      </button>
                    ) : (
                      <button
                        type="submit"
                        className="mobile-console__send-button"
                        disabled={submitDisabled}
                        aria-label="Send message"
                      >
                        <ArrowUp aria-hidden="true" />
                      </button>
                    )}
                  </div>
                </div>
                <p className="mobile-console__composer-hint">
                  Textarea input supports Android dictation · files upload to the gateway
                </p>
              </form>
            </section>
          )}

          {activeTab === "bots" && (
            <section className="mobile-console__panel" aria-labelledby="mobile-bots-title">
              <div className="mobile-console__panel-heading">
                <div>
                  <p className="mobile-console__eyebrow">OPERATORS</p>
                  <h2 id="mobile-bots-title">Choose a bot</h2>
                </div>
                <button
                  type="button"
                  className="mobile-console__icon-button"
                  onClick={() => void loadProfiles().then(refreshGatewayProfiles)}
                  disabled={profilesLoading}
                  aria-label="Refresh bots"
                >
                  <RefreshCw className={cn(profilesLoading && "mobile-spin")} aria-hidden="true" />
                </button>
              </div>
              <p className="mobile-console__intro">
                Selecting a bot opens its shared Windows Bot Chat on the connected Hermes
                gateway.
              </p>
              {profilesLoading && profiles.length === 0 ? (
                <div className="mobile-console__loading" aria-busy="true">
                  <RefreshCw className="mobile-spin" aria-hidden="true" /> Loading bots…
                </div>
              ) : profiles.length === 0 ? (
                <div className="mobile-console__empty mobile-console__empty--compact">
                  <Bot aria-hidden="true" />
                  <h3>No bots found.</h3>
                  <p>{profileError || "Create a bot in the advanced console first."}</p>
                </div>
              ) : (
                <div className="mobile-console__card-list">
                   {profiles.map((profile) => {
                     const selected = profile.name === selectedProfile;
                     const active = profile.name === activeProfileName;
                     const displayName = profileLabel(profile);
                     const avatar = botAvatar(profile.name);
                     const AvatarIcon = avatar.icon;
                     const avatarDataUrl = avatarDataUrls[profile.name];
                     return (
                      <button
                        type="button"
                        className={cn("mobile-profile-card", selected && "mobile-profile-card--selected")}
                        key={profile.name}
                        onClick={() => handleProfileSelect(profile.name)}
                        disabled={sessionBusy || stream.streaming || sending || stopping}
                        aria-label={`Chat with ${displayName}`}
                        aria-pressed={selected}
                      >
                        <span
                          className="mobile-profile-card__icon"
                          aria-hidden="true"
                          style={{
                            background: avatar.background,
                            color: avatar.foreground,
                            boxShadow: `0 0 18px ${avatar.glow}`,
                          }}
                        >
                          {avatarDataUrl ? (
                            <img
                              className="mobile-profile-card__avatar-image"
                              src={avatarDataUrl}
                              alt=""
                              onError={() => handleAvatarError(profile.name)}
                            />
                          ) : (
                            <AvatarIcon className="mobile-profile-card__avatar-glyph" />
                          )}
                          <span className="mobile-profile-card__monogram">
                            {botMonogram(displayName)}
                          </span>
                          {(selected || active) && (
                            <span className="mobile-profile-card__status">
                              <Check />
                            </span>
                          )}
                        </span>
                        <span className="mobile-profile-card__body">
                          <strong>{displayName}</strong>
                          <span>{profile.description || profile.model || "Hermes bot"}</span>
                          <small>
                            {active ? "ACTIVE · " : ""}
                            {profile.provider || "provider configured for bot"}
                          </small>
                          <span className="mobile-profile-card__chat-label">
                            Chat with {displayName}
                          </span>
                        </span>
                        <ChevronRight aria-hidden="true" />
                      </button>
                    );
                  })}
                </div>
              )}
              <div className="mobile-console__link-row">
                <Link to="/profiles?view=manage" className="mobile-console__text-button">
                  Manage bots <ExternalLink aria-hidden="true" />
                </Link>
                <Link to="/profiles/new" className="mobile-console__text-button">
                  Create a bot <Plus aria-hidden="true" />
                </Link>
              </div>
              <div className="mobile-console__callout">
                <ShieldCheck aria-hidden="true" />
                <p>
                  Your phone is a remote console, not a node. Models, tools, files, and credentials
                  stay with the Hermes gateway.
                </p>
              </div>
            </section>
          )}

          {activeTab === "sessions" && (
            <section className="mobile-console__panel" aria-labelledby="mobile-sessions-title">
              <div className="mobile-console__panel-heading">
                <div>
                  <p className="mobile-console__eyebrow">DURABLE MEMORY</p>
                  <h2 id="mobile-sessions-title">Sessions</h2>
                </div>
                <div className="mobile-console__heading-actions">
                  <button
                    type="button"
                    className="mobile-console__icon-button"
                    onClick={() => void refreshSessions()}
                    disabled={!online || sessionsLoading}
                    aria-label="Refresh sessions"
                  >
                    <RefreshCw className={cn(sessionsLoading && "mobile-spin")} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="mobile-console__small-button"
                    onClick={() => void handleNewSession()}
                    disabled={!online || sessionBusy || stream.streaming}
                  >
                    <Plus aria-hidden="true" />
                    <span>New</span>
                  </button>
                </div>
              </div>
              <p className="mobile-console__intro">
                Resume a durable conversation from the selected bot. Current bot: <strong>{selectedProfile || "default"}</strong>.
              </p>
              {sessions.length === 0 ? (
                <div className="mobile-console__empty mobile-console__empty--compact">
                  <History aria-hidden="true" />
                  <h3>{sessionsLoading ? "Loading sessions…" : "No saved sessions yet."}</h3>
                  <p>Send a message to create the first durable conversation.</p>
                </div>
              ) : (
                <div className="mobile-console__card-list">
                  {sessions.map((session) => {
                    const selected = session.id === durableSessionId;
                    return (
                      <button
                        type="button"
                        className={cn("mobile-session-card", selected && "mobile-session-card--selected")}
                        key={session.id}
                        onClick={() => void handleResume(session.id)}
                        disabled={!online || sessionBusy || stream.streaming}
                      >
                        <span className="mobile-session-card__body">
                          <strong>{session.title || "Untitled session"}</strong>
                          <span>{session.preview || "No preview yet"}</span>
                          <small>
                            {formatSessionDate(session.started_at)} · {session.message_count} messages
                          </small>
                        </span>
                        <ChevronRight aria-hidden="true" />
                      </button>
                    );
                  })}
                </div>
              )}
            </section>
          )}

          {activeTab === "more" && (
            <section className="mobile-console__panel" aria-labelledby="mobile-more-title">
              <div className="mobile-console__panel-heading">
                <div>
                  <p className="mobile-console__eyebrow">CONTROL SURFACE</p>
                  <h2 id="mobile-more-title">More</h2>
                </div>
                <Settings2 aria-hidden="true" />
              </div>
              <div className="mobile-console__callout mobile-console__callout--large">
                <Radio aria-hidden="true" />
                <div>
                  <strong>Remote console mode</strong>
                  <p>
                    This lightweight phone view talks to your existing Hermes gateway. It does not
                    install a model, expose credentials, or run work locally.
                  </p>
                </div>
              </div>
              <nav className="mobile-console__link-list" aria-label="Advanced console links">
                <Link to="/chat" className="mobile-console__link-card">
                  <span><Zap aria-hidden="true" /><strong>Advanced Console</strong><small>Full terminal and desktop chat</small></span>
                  <ChevronRight aria-hidden="true" />
                </Link>
                <Link to="/profiles?view=manage" className="mobile-console__link-card">
                  <span><Bot aria-hidden="true" /><strong>Bot management</strong><small>Configure bots and skills</small></span>
                  <ChevronRight aria-hidden="true" />
                </Link>
                <Link to="/config" className="mobile-console__link-card">
                  <span><Settings2 aria-hidden="true" /><strong>Settings</strong><small>Gateway and agent configuration</small></span>
                  <ChevronRight aria-hidden="true" />
                </Link>
                <Link to="/system" className="mobile-console__link-card">
                  <span><ShieldCheck aria-hidden="true" /><strong>System status</strong><small>Health, storage, and runtime checks</small></span>
                  <ChevronRight aria-hidden="true" />
                </Link>
                <Link to="/logs" className="mobile-console__link-card">
                  <span><FilePlus2 aria-hidden="true" /><strong>Logs</strong><small>Inspect recent gateway activity</small></span>
                  <ChevronRight aria-hidden="true" />
                </Link>
              </nav>
              <button type="button" className="mobile-console__reconnect" onClick={forceReconnect}>
                <RefreshCw aria-hidden="true" /> Reconnect gateway
              </button>
            </section>
          )}
        </div>
      </main>

      <nav className="mobile-console__tabs" aria-label="Mobile console sections">
        {MOBILE_TABS.map((tab) => {
          const Icon = tab.icon;
          const selected = activeTab === tab.id;
          return (
            <button
              type="button"
              className={cn("mobile-console__tab", selected && "mobile-console__tab--selected")}
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              aria-current={selected ? "page" : undefined}
            >
              <Icon aria-hidden="true" />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
