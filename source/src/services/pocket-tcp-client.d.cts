export class PocketTcpClient {
  constructor(socketFactory: (options: { host: string; port: number; connectTimeout: number }) => unknown);
  connect(host: string, port?: number): Promise<void>;
  request(command: string, fields?: Record<string, unknown>): Promise<Record<string, any>>;
  close(): void;
}
