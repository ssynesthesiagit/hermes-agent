import type { ProfileInfo } from "@/lib/api";

const CANONICAL_SESSION_TITLE = "Bot Chat";
const MAX_AVATAR_BYTES = 2_000_000;
const MAX_AVATAR_DATA_URL_LENGTH = 2_800_000;

export type MobileProfileInfo = ProfileInfo & {
  has_avatar?: boolean;
  ui_meta?: Record<string, unknown>;
};

export type GatewayProfileRow = Pick<MobileProfileInfo, "name"> &
  Partial<Omit<MobileProfileInfo, "name">>;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function mergeMobileProfiles(
  base: MobileProfileInfo[],
  updates: GatewayProfileRow[],
): MobileProfileInfo[] {
  const merged = new Map(base.map((profile) => [profile.name, profile]));
  for (const update of updates) {
    merged.set(update.name, {
      ...merged.get(update.name),
      ...update,
    } as MobileProfileInfo);
  }
  return [...merged.values()];
}

export function canonicalSessionCandidates(
  profile: MobileProfileInfo | undefined,
): string[] {
  const uiMeta = asRecord(profile?.ui_meta);
  const botMeta = asRecord(uiMeta?.["hermes-bots"]);
  const pinned = stringValue(botMeta?.chat);
  return pinned && pinned !== CANONICAL_SESSION_TITLE
    ? [pinned, CANONICAL_SESSION_TITLE]
    : [CANONICAL_SESSION_TITLE];
}

export function validateAvatarDataUrl(response: unknown): string | null {
  const payload = asRecord(response);
  if (payload?.found !== true) return null;

  const data = stringValue(payload.data);
  const mime = stringValue(payload.mime)?.toLowerCase();
  if (!data || !mime || data.length > MAX_AVATAR_DATA_URL_LENGTH) return null;

  const match = /^data:(image\/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]+={0,2})$/.exec(
    data,
  );
  if (!match || match[1] !== mime) return null;

  const reportedSize = payload.size;
  if (
    typeof reportedSize !== "number" ||
    !Number.isSafeInteger(reportedSize) ||
    reportedSize <= 0 ||
    reportedSize > MAX_AVATAR_BYTES
  ) {
    return null;
  }

  let binary: string;
  try {
    binary = atob(match[2]);
  } catch {
    return null;
  }
  if (binary.length !== reportedSize || binary.length > MAX_AVATAR_BYTES) {
    return null;
  }

  const byte = (index: number) => binary.charCodeAt(index);
  const isPng =
    binary.length >= 8 &&
    [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a].every(
      (value, index) => byte(index) === value,
    );
  const isJpeg =
    binary.length >= 3 && byte(0) === 0xff && byte(1) === 0xd8 && byte(2) === 0xff;
  const isWebp =
    binary.length >= 12 &&
    binary.slice(0, 4) === "RIFF" &&
    binary.slice(8, 12) === "WEBP";
  const signatureMatches =
    (mime === "image/png" && isPng) ||
    (mime === "image/jpeg" && isJpeg) ||
    (mime === "image/webp" && isWebp);

  return signatureMatches ? data : null;
}
