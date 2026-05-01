const PROVIDERS = [
  'Ollama (本地)',
  'llama.cpp (本地)',
  'OpenAI (雲端)',
  'OpenRouter (雲端)',
];

const PROVIDER_LABEL_KEYS = {
  'Ollama (本地)': 'routing.provider.ollama',
  'llama.cpp (本地)': 'routing.provider.llamacpp',
  'OpenAI (雲端)': 'routing.provider.openai',
  'OpenRouter (雲端)': 'routing.provider.openrouter',
};

const TASK_KEYS = [
  'chat',
  'expand',
  'pipeline',
  'compress',
  'distill',
  'ep_fuse',
  'profile',
  'persona_sync',
  'persona_seed',
  'background_gather',
  'character_gen',
  'router',
  'group_router',
  'translate',
  'browser',
];

function providerLabel(provider) {
  const key = PROVIDER_LABEL_KEYS[provider];
  return key ? MCI18N.t(key, {}, provider) : provider;
}

function taskInfo(taskKey) {
  return {
    desc: MCI18N.t(`routing.tasks.${taskKey}.desc`, {}, taskKey),
    help: MCI18N.t(`routing.tasks.${taskKey}.help`, {}, ''),
  };
}

function taskInfos() {
  return Object.fromEntries(TASK_KEYS.map(key => [key, taskInfo(key)]));
}
