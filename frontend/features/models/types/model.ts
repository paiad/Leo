export type WorkspaceModel = {
  id: string;
  name: string;
  provider: string;
  baseUrl: string;
  apiKey: string;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
};

export type WorkspaceModelInput = {
  name: string;
  provider: string;
  baseUrl: string;
  apiKey: string;
  enabled: boolean;
};
