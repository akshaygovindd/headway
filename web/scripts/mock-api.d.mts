export function respond(path: string): { status: number; body: unknown };
export function startMockApi(port?: number): Promise<import("node:http").Server>;
