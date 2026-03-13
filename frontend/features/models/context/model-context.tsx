"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { fetchChatModelConfig } from "@/features/chat/services/chat-api";
import {
  createModel as createModelApi,
  deleteModel as deleteModelApi,
  fetchActiveModel,
  fetchModels,
  setActiveModel as setActiveModelApi,
  updateModel as updateModelApi,
} from "@/features/models/services/models-api";
import type { WorkspaceModel, WorkspaceModelInput } from "@/features/models/types/model";

type ModelContextValue = {
  models: WorkspaceModel[];
  activeModelId: string | null;
  activeModel: WorkspaceModel | null;
  setActiveModelId: (id: string) => Promise<void>;
  createModel: (input: WorkspaceModelInput) => Promise<WorkspaceModel>;
  updateModel: (id: string, input: WorkspaceModelInput) => Promise<WorkspaceModel | null>;
  deleteModel: (id: string) => Promise<void>;
  reload: () => Promise<void>;
};
const ModelContext = createContext<ModelContextValue | null>(null);

export function ModelProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [models, setModels] = useState<WorkspaceModel[]>([]);
  const [activeModelId, setActiveModelId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [modelList, active] = await Promise.all([fetchModels(), fetchActiveModel()]);
      setModels(modelList);
      setActiveModelId(active?.id ?? modelList[0]?.id ?? null);
    } catch {
      const chatConfig = await fetchChatModelConfig();
      const fallback: WorkspaceModel = {
        id: "fallback-model",
        name: chatConfig.defaultModel || "unknown",
        provider: chatConfig.provider || "openai-compatible",
        baseUrl: chatConfig.defaultBaseUrl || "",
        apiKey: "",
        enabled: true,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };
      setModels([fallback]);
      setActiveModelId(fallback.id);
    }
  }, []);

  useEffect(() => {
    void reload().catch(() => {
      // Keep UI alive even when backend model APIs are unavailable.
    });
  }, [reload]);

  const value = useMemo<ModelContextValue>(() => {
    const activeModel = models.find((item) => item.id === activeModelId) ?? null;

    return {
      models,
      activeModelId,
      activeModel,
      reload,
      setActiveModelId: async (id: string) => {
        await setActiveModelApi(id);
        setActiveModelId(id);
      },
      createModel: async (input: WorkspaceModelInput) => {
        const nextModel = await createModelApi(input);
        setModels((prev) => [...prev, nextModel]);
        setActiveModelId(nextModel.id);
        await setActiveModelApi(nextModel.id);
        return nextModel;
      },
      updateModel: async (id: string, input: WorkspaceModelInput) => {
        const updated = await updateModelApi(id, input);
        setModels((prev) =>
          prev.map((item) => (item.id === id ? updated : item)),
        );
        return updated;
      },
      deleteModel: async (id: string) => {
        await deleteModelApi(id);
        await reload();
      },
    };
  }, [activeModelId, models, reload]);

  return <ModelContext.Provider value={value}>{children}</ModelContext.Provider>;
}

export function useModelContext() {
  const context = useContext(ModelContext);
  if (!context) {
    throw new Error("useModelContext 必须在 ModelProvider 内使用");
  }
  return context;
}
