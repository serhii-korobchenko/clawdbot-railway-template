#!/usr/bin/env python3
"""
Backport OpenClaw PR #97174 ("Fix Telegram plugin callback routing") onto
OpenClaw v2026.5.22 during the Docker build.

Why this exists:
- PROROK uses api.registerInteractiveHandler() for deterministic Telegram UI.
- In v2026.5.22, scoped plugin loads can clear the process-global interactive
  handler map while the live PluginRegistry still owns the plugin.
- Telegram then treats the callback as unmatched and sends raw callback_data
  into the normal agent flow.
- Upstream PR #97174 fixes this by making live plugin registries the lifecycle
  owner of interactive handlers and resolving callbacks through those registries.

This patch is deliberately fail-closed: every replacement must match exactly
once (or an explicitly expected number of times) against v2026.5.22.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/openclaw")


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(rel: str, old: str, new: str) -> None:
    text = read(rel)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{rel}: expected one patch anchor, found {count}: {old[:100]!r}")
    write(rel, text.replace(old, new, 1))


def replace_exact(rel: str, old: str, new: str, expected: int) -> None:
    text = read(rel)
    count = text.count(old)
    if count != expected:
        raise SystemExit(
            f"{rel}: expected {expected} patch anchors, found {count}: {old[:100]!r}"
        )
    write(rel, text.replace(old, new))


# 1) Allow registry-backed lookup adapters (only .get() is required).
replace_once(
    "src/plugins/interactive-shared.ts",
    '  interactiveHandlers: Map<string, TRegistration>;\n',
    '  interactiveHandlers: Pick<ReadonlyMap<string, TRegistration>, "get">;\n',
)

# 2) Mark compatibility/global registrations that are lifecycle-owned by a registry.
replace_once(
    "src/plugins/interactive-state.ts",
    "  pluginRoot?: string;\n};\n",
    "  pluginRoot?: string;\n  registryOwned?: true;\n};\n",
)

# 3) Add registry-owned resolver and registration mode.
replace_once(
    "src/plugins/interactive-registry.ts",
    '''export function registerPluginInteractiveHandler(
  pluginId: string,
  registration: PluginInteractiveHandlerRegistration,
  opts?: { pluginName?: string; pluginRoot?: string },
): InteractiveRegistrationResult {
''',
    '''export function resolvePluginInteractiveRegistrationsMatch(
  registrations: readonly RegisteredInteractiveHandler[],
  channel: string,
  data: string,
): { registration: RegisteredInteractiveHandler; namespace: string; payload: string } | null {
  return resolvePluginInteractiveMatch({
    interactiveHandlers: {
      get: (key) =>
        registrations.find(
          (registration) =>
            toPluginInteractiveRegistryKey(registration.channel, registration.namespace) === key,
        ),
    },
    channel,
    data,
  });
}

export function registerPluginInteractiveHandler(
  pluginId: string,
  registration: PluginInteractiveHandlerRegistration,
  opts?: { pluginName?: string; pluginRoot?: string; registryOwned?: true },
): InteractiveRegistrationResult {
''',
)
replace_once(
    "src/plugins/interactive-registry.ts",
    "    pluginName: opts?.pluginName,\n    pluginRoot: opts?.pluginRoot,\n",
    "    pluginName: opts?.pluginName,\n    pluginRoot: opts?.pluginRoot,\n    registryOwned: opts?.registryOwned,\n",
)
replace_once(
    "src/plugins/interactive-registry.ts",
    '''  return { ok: true };
}

export function clearPluginInteractiveHandlers(): void {
''',
    '''  return { ok: true };
}

export function registerRegistryPluginInteractiveHandler(
  pluginId: string,
  registration: PluginInteractiveHandlerRegistration,
  opts?: { pluginName?: string; pluginRoot?: string },
): InteractiveRegistrationResult {
  return registerPluginInteractiveHandler(pluginId, registration, {
    ...opts,
    registryOwned: true,
  });
}

export function clearPluginInteractiveHandlers(): void {
''',
)

# 4) Expose the set of registries that are actually live (active/pinned surfaces).
replace_once(
    "src/plugins/runtime.ts",
    "function collectLivePluginAgentEventRegistries(): PluginRegistry[] {\n",
    "export function collectLivePluginRegistries(): PluginRegistry[] {\n",
)
replace_exact(
    "src/plugins/runtime.ts",
    "collectLivePluginAgentEventRegistries()",
    "collectLivePluginRegistries()",
    2,
)

# 5) Resolve interactive callbacks through live registry owners.
replace_once(
    "src/plugins/interactive.ts",
    'import { resolvePluginInteractiveNamespaceMatch } from "./interactive-registry.js";\n',
    '''import {
  resolvePluginInteractiveNamespaceMatch,
  resolvePluginInteractiveRegistrationsMatch,
} from "./interactive-registry.js";
''',
)
replace_once(
    "src/plugins/interactive.ts",
    '} from "./interactive-state.js";\n',
    '} from "./interactive-state.js";\nimport { collectLivePluginRegistries } from "./runtime.js";\n',
)
replace_once(
    "src/plugins/interactive.ts",
    '''export type { InteractiveRegistrationResult } from "./interactive-registry.js";

export async function dispatchPluginInteractiveHandler<
''',
    '''export type { InteractiveRegistrationResult } from "./interactive-registry.js";

function resolveLivePluginInteractiveNamespaceMatch(channel: string, data: string) {
  const existing = resolvePluginInteractiveNamespaceMatch(channel, data);
  if (existing && existing.registration.registryOwned !== true) {
    return existing;
  }

  for (const registry of collectLivePluginRegistries()) {
    const match = resolvePluginInteractiveRegistrationsMatch(
      registry.interactiveHandlers ?? [],
      channel,
      data,
    );
    if (match) {
      return match;
    }
  }
  return null;
}

export async function dispatchPluginInteractiveHandler<
''',
)
replace_once(
    "src/plugins/interactive.ts",
    "  const match = resolvePluginInteractiveNamespaceMatch(params.channel, params.data);\n",
    "  const match = resolveLivePluginInteractiveNamespaceMatch(params.channel, params.data);\n",
)

# 6) Store interactive handlers inside PluginRegistry.
replace_once(
    "src/plugins/registry-types.ts",
    "  PluginLogger,\n",
    "  PluginInteractiveHandlerRegistration,\n  PluginLogger,\n",
)
replace_once(
    "src/plugins/registry-types.ts",
    '''export type PluginCommandRegistration = {
  pluginId: string;
  pluginName?: string;
  command: OpenClawPluginCommandDefinition;
  source: string;
  rootDir?: string;
};

''',
    '''export type PluginCommandRegistration = {
  pluginId: string;
  pluginName?: string;
  command: OpenClawPluginCommandDefinition;
  source: string;
  rootDir?: string;
};

export type PluginInteractiveHandlerRegistryRegistration =
  PluginInteractiveHandlerRegistration & {
    pluginId: string;
    pluginName?: string;
    pluginRoot?: string;
  };

''',
)
replace_once(
    "src/plugins/registry-types.ts",
    "  commands: PluginCommandRegistration[];\n",
    "  commands: PluginCommandRegistration[];\n  interactiveHandlers?: PluginInteractiveHandlerRegistryRegistration[];\n",
)
replace_once(
    "src/plugins/registry-empty.ts",
    "    commands: [],\n",
    "    commands: [],\n    interactiveHandlers: [],\n",
)

# 7) Make plugin API registration registry-owned, while keeping compatibility state.
replace_once(
    "src/plugins/registry.ts",
    "  registerPluginInteractiveHandler,\n",
    "  registerRegistryPluginInteractiveHandler,\n",
)
replace_once(
    "src/plugins/registry.ts",
    '''              registerInteractiveHandler: (registration) => {
                const result = registerPluginInteractiveHandler(record.id, registration, {
                  pluginName: record.name,
                  pluginRoot: record.rootDir,
                });
                if (!result.ok) {
                  pushDiagnostic({
                    level: "warn",
                    pluginId: record.id,
                    source: record.source,
                    message: result.error ?? "interactive handler registration failed",
                  });
                }
              },
''',
    '''              registerInteractiveHandler: (registration) => {
                const result = registerRegistryPluginInteractiveHandler(record.id, registration, {
                  pluginName: record.name,
                  pluginRoot: record.rootDir,
                });
                if (!result.ok) {
                  pushDiagnostic({
                    level: "warn",
                    pluginId: record.id,
                    source: record.source,
                    message: result.error ?? "interactive handler registration failed",
                  });
                  return;
                }
                registry.interactiveHandlers ??= [];
                registry.interactiveHandlers.push({
                  ...registration,
                  pluginId: record.id,
                  pluginName: record.name,
                  pluginRoot: record.rootDir,
                });
              },
''',
)

# 8) Include the new registry array in loader rollback snapshots.
replace_once(
    "src/plugins/loader.ts",
    '    commands: PluginRegistry["commands"];\n',
    '    commands: PluginRegistry["commands"];\n    interactiveHandlers: NonNullable<PluginRegistry["interactiveHandlers"]>;\n',
)
replace_once(
    "src/plugins/loader.ts",
    "      commands: [...registry.commands],\n",
    "      commands: [...registry.commands],\n      interactiveHandlers: [...(registry.interactiveHandlers ?? [])],\n",
)
replace_once(
    "src/plugins/loader.ts",
    "  registry.commands = snapshot.arrays.commands;\n",
    "  registry.commands = snapshot.arrays.commands;\n  registry.interactiveHandlers = snapshot.arrays.interactiveHandlers;\n",
)

# Fail-closed sanity markers.
checks = {
    "src/plugins/interactive.ts": [
        "resolveLivePluginInteractiveNamespaceMatch",
        "collectLivePluginRegistries",
    ],
    "src/plugins/registry.ts": [
        "registerRegistryPluginInteractiveHandler",
        "registry.interactiveHandlers.push",
    ],
    "src/plugins/registry-types.ts": ["interactiveHandlers?:"],
    "src/plugins/loader.ts": ["snapshot.arrays.interactiveHandlers"],
}
for rel, needles in checks.items():
    text = read(rel)
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{rel}: backport verification failed; missing {needle!r}")

print("OpenClaw v2026.5.22 Telegram plugin callback backport applied")
