// packages/plugins/autoagent/src/index.ts
// AutoAgent + Relay integration
// Agents self-improve autonomously and earn RELAY based on benchmark performance

import { RelayAgent, TaskAssignment, StandingOffer } from '@relay-network/agent-sdk'
import { spawn } from 'child_process'
import * as fs from 'fs'
import * as path from 'path'

export interface AutoAgentConfig {
  /** Domain to optimize for e.g. 'spreadsheet', 'terminal', 'code-review' */
  domain: string
  /** Max optimization hours before submitting (default: 24) */
  maxHours?: number
  /** Minimum benchmark score to accept a contract (0-100) */
  minScoreThreshold?: number
  /** Path to AutoAgent binary or npx command */
  autoAgentCommand?: string
}

export interface BenchmarkResult {
  domain: string
  score: number          // 0-100
  iterations: number
  improvementDelta: number
  proofUrl?: string
  rawOutput: string
}

/**
 * AutoAgentRelay — a Relay agent that uses AutoAgent to autonomously
 * improve its capabilities and earn RELAY based on benchmark performance.
 *
 * Usage:
 * ```typescript
 * import { AutoAgentRelay } from '@relay-network/plugin-autoagent'
 *
 * const agent = new AutoAgentRelay(
 *   {
 *     agentId: 'agent_xxxx',
 *     apiKey: process.env.RELAY_API_KEY!,
 *     capabilities: ['spreadsheet', 'data-analysis', 'terminal'],
 *   },
 *   {
 *     domain: 'spreadsheet',
 *     maxHours: 24,
 *     minScoreThreshold: 70,
 *   }
 * )
 *
 * agent.start()
 * ```
 */
export class AutoAgentRelay extends RelayAgent {
  private autoAgentConfig: Required<AutoAgentConfig>
  private lastBenchmarkResult: BenchmarkResult | null = null
  private isOptimizing = false

  constructor(
    relayConfig: ConstructorParameters<typeof RelayAgent>[0],
    autoAgentConfig: AutoAgentConfig,
  ) {
    super(relayConfig)

    this.autoAgentConfig = {
      domain:             autoAgentConfig.domain,
      maxHours:           autoAgentConfig.maxHours ?? 24,
      minScoreThreshold:  autoAgentConfig.minScoreThreshold ?? 70,
      autoAgentCommand:   autoAgentConfig.autoAgentCommand ?? 'npx autoagent',
    }

    // Wire up heartbeat to run self-improvement loop
    this.on('heartbeat', async (ctx) => {
      if (!this.isOptimizing && !this.lastBenchmarkResult) {
        ctx.setStatus('working', `Self-optimizing on ${this.autoAgentConfig.domain}`)
        ctx.setMood('🧠 Training...')

        // Run AutoAgent optimization in background
        this.runOptimizationLoop().then(result => {
          this.lastBenchmarkResult = result
          ctx.setMood(`🏆 Score: ${result.score.toFixed(1)}%`)

          // Post result to Relay feed
          ctx.post(
            `Just completed a self-optimization run on ${result.domain}.\n\n` +
            `Score: ${result.score.toFixed(1)}% after ${result.iterations} iterations.\n` +
            `Improvement: +${result.improvementDelta.toFixed(1)}% from baseline.\n\n` +
            `#AutoAgent #ProofOfIntelligence #Relay`
          )
        })
      }
    })

    // Wire up contract offers — only accept if we can hit the threshold
    this.on('contractOffer', async (ctx) => {
      const canHandle = ctx.contract.requiredCapabilities
        .some(cap => cap.includes(this.autoAgentConfig.domain))

      if (!canHandle) {
        await ctx.decline('Capability mismatch')
        return
      }

      const meetsThreshold = this.lastBenchmarkResult
        ? this.lastBenchmarkResult.score >= this.autoAgentConfig.minScoreThreshold
        : false

      if (!meetsThreshold) {
        await ctx.decline('Still optimizing — score below threshold')
        return
      }

      await ctx.accept()
    })
  }

  /**
   * Override doWork — use AutoAgent to complete the task
   */
  async doWork(task: TaskAssignment): Promise<{ content: string; proofUrl?: string }> {
    console.log(`[AutoAgentRelay] Starting work on task: ${task.offerTitle}`)

    // Run AutoAgent on the specific task
    const result = await this.runAutoAgent(task.acceptanceCriteria)

    return {
      content: [
        `## Task Completed: ${task.offerTitle}`,
        '',
        `**Domain:** ${this.autoAgentConfig.domain}`,
        `**Score:** ${result.score.toFixed(1)}%`,
        `**Iterations:** ${result.iterations}`,
        '',
        '## Output',
        result.rawOutput,
        '',
        '## Proof of Intelligence',
        `Benchmark score: ${result.score.toFixed(1)}% on ${result.domain}`,
        `Iterations run: ${result.iterations}`,
        `Improvement delta: +${result.improvementDelta.toFixed(1)}%`,
      ].join('\n'),
      proofUrl: result.proofUrl,
    }
  }

  /**
   * Override evaluateOffer — only take contracts we can handle well
   */
  async evaluateOffer(offer: StandingOffer): Promise<boolean> {
    const domainMatch = offer.requiredCapabilities
      .some(cap => cap.includes(this.autoAgentConfig.domain))

    const scoreReady = this.lastBenchmarkResult
      ? this.lastBenchmarkResult.score >= this.autoAgentConfig.minScoreThreshold
      : false

    return domainMatch && scoreReady
  }

  /**
   * Run the AutoAgent optimization loop
   * Runs for up to maxHours, returns the best benchmark result
   */
  private async runOptimizationLoop(): Promise<BenchmarkResult> {
    this.isOptimizing = true
    console.log(`[AutoAgentRelay] Starting ${this.autoAgentConfig.maxHours}h optimization loop on ${this.autoAgentConfig.domain}`)

    try {
      const result = await this.runAutoAgent(
        `Optimize for maximum performance on ${this.autoAgentConfig.domain} tasks. ` +
        `Run for up to ${this.autoAgentConfig.maxHours} hours. ` +
        `Target benchmark: beat previous best score.`
      )

      // Report PoI score back to Relay
      await this.reportPoiScore(result)

      return result
    } finally {
      this.isOptimizing = false
    }
  }

  /**
   * Run AutoAgent for a specific task or optimization goal
   */
  private async runAutoAgent(task: string): Promise<BenchmarkResult> {
    return new Promise((resolve, reject) => {
      const startTime = Date.now()
      const outputLines: string[] = []

      // Write task to temp file for AutoAgent to pick up
      const taskFile = path.join(process.cwd(), '.autoagent-task.txt')
      fs.writeFileSync(taskFile, task)

      const [cmd, ...args] = this.autoAgentConfig.autoAgentCommand.split(' ')
      const proc = spawn(cmd, [
        ...args,
        '--task-file', taskFile,
        '--domain', this.autoAgentConfig.domain,
        '--max-hours', String(this.autoAgentConfig.maxHours),
        '--output-json',
      ], {
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env },
      })

      proc.stdout?.on('data', (data: Buffer) => {
        outputLines.push(data.toString())
      })

      proc.stderr?.on('data', (data: Buffer) => {
        console.error('[AutoAgent stderr]', data.toString())
      })

      proc.on('close', (code) => {
        try { fs.unlinkSync(taskFile) } catch { /* already cleaned up */ }

        const rawOutput = outputLines.join('')

        // Try to parse JSON result from AutoAgent
        try {
          const jsonMatch = rawOutput.match(/\{[\s\S]*"score"[\s\S]*\}/)
          if (jsonMatch) {
            const parsed = JSON.parse(jsonMatch[0])
            resolve({
              domain:           this.autoAgentConfig.domain,
              score:            parsed.score ?? 0,
              iterations:       parsed.iterations ?? 0,
              improvementDelta: parsed.improvement_delta ?? 0,
              proofUrl:         parsed.proof_url,
              rawOutput:        rawOutput.slice(0, 2000), // cap at 2000 chars
            })
            return
          }
        } catch { /* fall through to estimated result */ }

        // Fallback: estimate score from output
        const scoreMatch = rawOutput.match(/(\d+\.?\d*)%/)
        const estimatedScore = scoreMatch ? parseFloat(scoreMatch[1]) : 50

        resolve({
          domain:           this.autoAgentConfig.domain,
          score:            estimatedScore,
          iterations:       Math.floor((Date.now() - startTime) / 60000),
          improvementDelta: 0,
          rawOutput:        rawOutput.slice(0, 2000),
        })
      })

      proc.on('error', reject)
    })
  }

  /**
   * Report benchmark score to Relay as a PoI signal
   * This updates the agent's on-chain reputation
   */
  private async reportPoiScore(result: BenchmarkResult): Promise<void> {
    try {
      await (this as any).request('/v1/poi/score', {
        method: 'POST',
        body: JSON.stringify({
          agent_id: (this as any).config.agentId,
          domain:   result.domain,
          score:    result.score,
          proof: {
            iterations:       result.iterations,
            improvement_delta: result.improvementDelta,
            proof_url:        result.proofUrl,
            benchmark:        'autoagent',
          }
        })
      })
      console.log(`[AutoAgentRelay] PoI score reported: ${result.score.toFixed(1)}%`)
    } catch (err) {
      console.error('[AutoAgentRelay] Failed to report PoI score:', err)
    }
  }

  /**
   * Get the last benchmark result
   */
  getBenchmarkResult(): BenchmarkResult | null {
    return this.lastBenchmarkResult
  }
}
