// Quests — gamification page. Shows the player's XP/level, active quests with
// their objectives, completed quests, and unlocked achievements. All state is
// local-first on the backend; this page is a read + complete surface over it.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Circle, Target, Trophy, Zap } from 'lucide-react'
import { Badge, Btn, Card, CardTitle, EmptyState, Skeleton } from '../../components/ui'
import { i18nT } from '../../i18n/t'
import { questsApi, type Quest, type QuestObjective } from './api'

function ObjectiveRow({ objective, onComplete }: { objective: QuestObjective; onComplete?: () => void }) {
  return (
    <div className="flex items-center gap-2 py-1">
      {objective.completed ? (
        <CheckCircle2 size={15} className="lucide-inline shrink-0 text-ok" />
      ) : (
        <Circle size={15} className="lucide-inline shrink-0 text-muted" />
      )}
      <span className={`text-[13px] min-w-0 ${objective.completed ? 'text-muted line-through' : 'text-text'}`}>
        {objective.description}
      </span>
      {!objective.completed && onComplete && (
        <Btn className="ml-auto !px-2 !py-0.5 !text-[12px]" onClick={onComplete}>
          {i18nT('apps.quests.questsPage.complete_objective')}
        </Btn>
      )}
    </div>
  )
}

function QuestCard({ quest, onCompleteQuest, onCompleteObjective }: {
  quest: Quest
  onCompleteQuest: () => void
  onCompleteObjective: (objectiveId: string) => void
}) {
  return (
    <Card className="!mb-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Target size={15} className="lucide-inline shrink-0 text-accent" />
            <span className="text-sm font-semibold text-text-strong truncate">{quest.name}</span>
          </div>
          {quest.description && (
            <p className="mt-1 text-[13px] text-muted">{quest.description}</p>
          )}
        </div>
        <Badge variant="muted" className="shrink-0">
          <Zap size={12} className="lucide-inline" />
          {i18nT('apps.quests.questsPage.xp_reward', { xp: quest.xp_reward })}
        </Badge>
      </div>
      {quest.objectives.length > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          {quest.objectives.map((o) => (
            <ObjectiveRow key={o.id} objective={o} onComplete={() => onCompleteObjective(o.id)} />
          ))}
        </div>
      )}
      <div className="mt-3 flex justify-end">
        <Btn primary onClick={onCompleteQuest}>
          {i18nT('apps.quests.questsPage.complete_quest')}
        </Btn>
      </div>
    </Card>
  )
}

export default function QuestsPage() {
  const queryClient = useQueryClient()

  const xpQuery = useQuery({ queryKey: ['quests', 'xp'], queryFn: questsApi.xp })
  const activeQuery = useQuery({ queryKey: ['quests', 'active'], queryFn: questsApi.active })
  const completedQuery = useQuery({ queryKey: ['quests', 'completed'], queryFn: questsApi.completed })
  const achievementsQuery = useQuery({ queryKey: ['quests', 'achievements'], queryFn: questsApi.achievements })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['quests'] })
  }

  const completeQuest = useMutation({
    mutationFn: questsApi.completeQuest,
    onSuccess: invalidate,
  })
  const completeObjective = useMutation({
    mutationFn: ({ questId, objectiveId }: { questId: string; objectiveId: string }) =>
      questsApi.completeObjective(questId, objectiveId),
    onSuccess: invalidate,
  })

  const loading = xpQuery.isLoading || activeQuery.isLoading
  const error = xpQuery.error || activeQuery.error || completedQuery.error || achievementsQuery.error

  if (loading) {
    return (
      <div className="p-5">
        <Skeleton className="h-24 w-full rounded-lg" />
        <Skeleton className="h-40 w-full rounded-lg mt-4" />
        <Skeleton className="h-40 w-full rounded-lg mt-4" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-5">
        <EmptyState
          icon={<Trophy size={40} />}
          title={i18nT('apps.quests.questsPage.error')}
          subtitle={error instanceof Error ? error.message : undefined}
        />
      </div>
    )
  }

  const xp = xpQuery.data
  const active = activeQuery.data ?? []
  const completed = completedQuery.data ?? []
  const achievements = achievementsQuery.data ?? []

  return (
    <div className="p-5 max-w-3xl">
      {/* Player header */}
      <Card>
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent/15 text-accent">
            <Trophy size={22} className="lucide-inline" />
          </div>
          <div className="min-w-0">
            <div className="text-lg font-semibold text-text-strong">{xp?.level_title}</div>
            <div className="text-[13px] text-muted">
              {i18nT('apps.quests.questsPage.level', { level: xp?.level ?? 0 })} ·{' '}
              {i18nT('apps.quests.questsPage.xp', { xp: xp?.total_xp ?? 0 })}
            </div>
          </div>
        </div>
      </Card>

      {/* Active quests */}
      <CardTitle className="mt-5">
        <Target size={15} className="lucide-inline" />
        {i18nT('apps.quests.questsPage.active_quests')}
      </CardTitle>
      {active.length === 0 ? (
        <EmptyState
          icon={<Target size={40} />}
          title={i18nT('apps.quests.questsPage.no_active_quests')}
        />
      ) : (
        active.map((q) => (
          <QuestCard
            key={q.id}
            quest={q}
            onCompleteQuest={() => completeQuest.mutate(q.id)}
            onCompleteObjective={(objectiveId) => completeObjective.mutate({ questId: q.id, objectiveId })}
          />
        ))
      )}

      {/* Completed quests */}
      <CardTitle className="mt-5">
        <CheckCircle2 size={15} className="lucide-inline" />
        {i18nT('apps.quests.questsPage.completed_quests')}
      </CardTitle>
      {completed.length === 0 ? (
        <EmptyState
          icon={<CheckCircle2 size={40} />}
          title={i18nT('apps.quests.questsPage.no_completed_quests')}
        />
      ) : (
        completed.map((q) => (
          <Card key={q.id} className="!mb-3">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-medium text-text-strong truncate">{q.name}</span>
              <Badge variant="ok">
                <Zap size={12} className="lucide-inline" />
                {i18nT('apps.quests.questsPage.xp_reward', { xp: q.xp_reward })}
              </Badge>
            </div>
          </Card>
        ))
      )}

      {/* Achievements */}
      <CardTitle className="mt-5">
        <Trophy size={15} className="lucide-inline" />
        {i18nT('apps.quests.questsPage.achievements')}
      </CardTitle>
      {achievements.length === 0 ? (
        <EmptyState
          icon={<Trophy size={40} />}
          title={i18nT('apps.quests.questsPage.no_achievements')}
        />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {achievements.map((a) => (
            <Card key={a.id} className="!mb-0">
              <div className="flex items-center gap-2">
                <Trophy size={15} className="lucide-inline shrink-0 text-accent" />
                <span className="text-sm font-medium text-text-strong truncate">{a.name}</span>
              </div>
              {a.description && <p className="mt-1 text-[13px] text-muted">{a.description}</p>}
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
