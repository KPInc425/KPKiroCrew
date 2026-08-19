// Thin fetch wrapper for the Quests backend (registered directly on the main
// gateway's aiohttp Application — see backend/routes.py:register_routes — so the
// base path is /api/apps/quests, matching issue-radar's convention).

const API = '/api/apps/quests'

export interface QuestObjective {
  id: string
  description: string
  completed: boolean
  completed_at: string
}

export interface Quest {
  id: string
  name: string
  description: string
  objectives: QuestObjective[]
  xp_reward: number
  status: string
  created_at: string
  updated_at: string
  completed_at: string
}

export interface XpTotal {
  total_xp: number
  level: number
  level_title: string
}

export interface Achievement {
  id: string
  name: string
  description: string
  unlocked_at: string
  category: string
}

export interface CompleteQuestResult {
  quest: Quest
  xp_awarded: number
  flavor_text: string
  level_up: boolean
  new_level: number
  new_title: string
  message?: string
}

async function parseErrorBody(r: Response): Promise<string> {
  try {
    const body = await r.json()
    return body?.message || body?.error || `Request failed (${r.status})`
  } catch {
    return `Request failed (${r.status})`
  }
}

export const questsApi = {
  active: async (): Promise<Quest[]> => {
    const r = await fetch(`${API}/quests`, { credentials: 'same-origin' })
    if (!r.ok) throw new Error(await parseErrorBody(r))
    const data = await r.json()
    return data.quests ?? []
  },

  completed: async (): Promise<Quest[]> => {
    const r = await fetch(`${API}/quests/completed`, { credentials: 'same-origin' })
    if (!r.ok) throw new Error(await parseErrorBody(r))
    const data = await r.json()
    return data.quests ?? []
  },

  xp: async (): Promise<XpTotal> => {
    const r = await fetch(`${API}/xp`, { credentials: 'same-origin' })
    if (!r.ok) throw new Error(await parseErrorBody(r))
    return r.json()
  },

  achievements: async (): Promise<Achievement[]> => {
    const r = await fetch(`${API}/achievements`, { credentials: 'same-origin' })
    if (!r.ok) throw new Error(await parseErrorBody(r))
    const data = await r.json()
    return data.achievements ?? []
  },

  completeQuest: async (id: string): Promise<CompleteQuestResult> => {
    const r = await fetch(`${API}/quests/${encodeURIComponent(id)}/complete`, {
      method: 'POST',
      credentials: 'same-origin',
    })
    if (!r.ok) throw new Error(await parseErrorBody(r))
    return r.json()
  },

  completeObjective: async (questId: string, objectiveId: string): Promise<Quest> => {
    const r = await fetch(
      `${API}/quests/${encodeURIComponent(questId)}/objectives/${encodeURIComponent(objectiveId)}/complete`,
      { method: 'POST', credentials: 'same-origin' },
    )
    if (!r.ok) throw new Error(await parseErrorBody(r))
    const data = await r.json()
    return data.quest
  },
}
