import { describe, expect, it } from "vitest";

import {
  canonicalSessionCandidates,
  mergeMobileProfiles,
  validateAvatarDataUrl,
} from "./mobile-bots";

describe("mobile bot identity helpers", () => {
  it("prefers the desktop-pinned Bot Chat and falls back to its title", () => {
    expect(
      canonicalSessionCandidates({
        ui_meta: { "hermes-bots": { chat: "yatima-canonical-session" } },
      } as never),
    ).toEqual(["yatima-canonical-session", "Bot Chat"]);
    expect(canonicalSessionCandidates(undefined)).toEqual(["Bot Chat"]);
  });

  it("merges gateway-only bot metadata without dropping REST profile fields", () => {
    const profiles = mergeMobileProfiles(
      [{ name: "yatima", description: "Research bot" } as never],
      [
        {
          name: "yatima",
          has_avatar: true,
          ui_meta: { "hermes-bots": { chat: "shared-chat" } },
        },
      ],
    );

    expect(profiles[0]).toMatchObject({
      name: "yatima",
      description: "Research bot",
      has_avatar: true,
      ui_meta: { "hermes-bots": { chat: "shared-chat" } },
    });
  });

  it("accepts a bounded image response and rejects MIME, size, or signature mismatches", () => {
    const png = "data:image/png;base64,iVBORw0KGgo=";
    const size = atob(png.split(",")[1]).length;

    expect(validateAvatarDataUrl({ found: true, mime: "image/png", size, data: png })).toBe(
      png,
    );
    expect(
      validateAvatarDataUrl({ found: true, mime: "image/jpeg", size, data: png }),
    ).toBeNull();
    expect(
      validateAvatarDataUrl({ found: true, mime: "image/png", size: size + 1, data: png }),
    ).toBeNull();
    expect(
      validateAvatarDataUrl({
        found: true,
        mime: "image/png",
        size: 6,
        data: "data:image/png;base64,ZmFrZXBuZw==",
      }),
    ).toBeNull();
  });
});
