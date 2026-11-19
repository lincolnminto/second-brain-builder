export function shouldAutoFit(manuallyMoved: boolean, previous: ReadonlySet<string>, paths: readonly string[]): boolean {
  return !manuallyMoved && paths.some(path => !previous.has(path));
}
