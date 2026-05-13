export type PipelineMode = "stt_llm_tts";

export type AgentConfig = {
  agent_name: string;
  owner_context: string | null;
  system_prompt: string;
  knowledge_base: string;
  pipeline_mode: PipelineMode;
  is_enabled: boolean;
};

export type AgentConfigPatch = Partial<AgentConfig>;
