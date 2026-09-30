const PREFIX_A = "reconstruct-a:";
const PREFIX_B = "reconstruct-b:";

export function clearReconstructSessionState() {
  try {
    const keys: string[] = [];
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const k = sessionStorage.key(i);
      if (!k) continue;
      if (k.startsWith(PREFIX_A) || k.startsWith(PREFIX_B)) {
        keys.push(k);
      }
    }
    keys.forEach((k) => sessionStorage.removeItem(k));
  } catch {}
}
