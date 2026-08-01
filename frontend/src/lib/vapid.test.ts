import { describe, expect, it } from "vitest";
import { urlBase64ToUint8Array } from "./vapid";

describe("urlBase64ToUint8Array", () => {
  it("decodes a plain base64url string with no padding needed", () => {
    // "hello" in base64url
    const result = urlBase64ToUint8Array("aGVsbG8");
    expect(new TextDecoder().decode(result)).toBe("hello");
  });

  it("decodes a string requiring padding", () => {
    // "hi" -> base64 "aGk=" -> base64url "aGk"
    const result = urlBase64ToUint8Array("aGk");
    expect(new TextDecoder().decode(result)).toBe("hi");
  });

  it("converts URL-safe characters (- and _) back to standard base64 alphabet", () => {
    // Bytes chosen so the standard base64 encoding contains '+' and '/'.
    const bytes = new Uint8Array([0xfb, 0xff, 0xbf]);
    const standard = btoa(String.fromCharCode(...bytes)); // "+/+/" family
    const urlSafe = standard.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const result = urlBase64ToUint8Array(urlSafe);
    expect(Array.from(result)).toEqual(Array.from(bytes));
  });
});
