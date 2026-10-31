import { ChartCard } from './Dashboards'

export default function VisualizationCanvas({
  artifacts,
  activeArtifactId,
  onSelectArtifact,
  selectedTable,
  conversationScopeFixed = false,
  onSave,
  onReturnToConversation,
  onOpenDataExplorer,
  dataExplorerOpen,
}) {
  const activeArtifact = artifacts.find((artifact) => artifact.id === activeArtifactId)
    ?? artifacts[artifacts.length - 1]

  return (
    <section className="adaptive-visualization-canvas" aria-label="Visualization Canvas">
      <header className="adaptive-canvas-heading">
        <div>
          <p className="eyebrow">Analytical workspace</p>
          <h2>{selectedTable ? selectedTable.name : activeArtifact?.title ?? 'Results canvas'}</h2>
          <p className="adaptive-canvas-context">
            {selectedTable
              ? `${selectedTable.fileName} · ${Number.isFinite(selectedTable.rowCount) ? selectedTable.rowCount.toLocaleString() : 'Rows unavailable'} rows · ${Number.isFinite(selectedTable.columnCount) ? selectedTable.columnCount : 'Columns unavailable'} columns`
              : activeArtifact ? 'Generated from this conversation' : 'Evidence-backed results from this conversation'}
          </p>
        </div>
        <div className="adaptive-canvas-actions">
          {!dataExplorerOpen && (
            <button type="button" className="button button-secondary" onClick={onOpenDataExplorer}>
              Open Data Explorer
            </button>
          )}
          <button type="button" className="button button-secondary" onClick={onReturnToConversation}>
            Conversation
          </button>
        </div>
      </header>

      {selectedTable ? (
        <div className="adaptive-table-inspection">
          <h3>{selectedTable.fileName} · {selectedTable.name}</h3>
          <p>
            {Number.isFinite(selectedTable.rowCount) ? `${selectedTable.rowCount.toLocaleString()} rows` : 'Row count unavailable'}
            {' · '}
            {Number.isFinite(selectedTable.columnCount) ? `${selectedTable.columnCount} columns` : 'Column count unavailable'}
          </p>
          {selectedTable.columns.length > 0 ? (
            <div className="adaptive-column-grid" aria-label="Table columns">
              {selectedTable.columns.map((column) => <span key={column}>{column}</span>)}
            </div>
          ) : (
            <p className="adaptive-panel-empty">Column details are not present in the available dataset metadata.</p>
          )}
          <p className="adaptive-preview-unavailable">
            Row preview is not provided by the current dataset API. No sample records are being inferred.
          </p>
          {conversationScopeFixed && (
            <p className="adaptive-scope-notice" role="note">
              This table selection is for inspection only; it does not change the active conversation&apos;s data scope.
            </p>
          )}
        </div>
      ) : artifacts.length === 0 ? (
        <div className="adaptive-canvas-empty">
          <span aria-hidden="true">◫</span>
          <h3>No visualization results yet</h3>
          <p>
            This workspace opens when the assistant returns supported charts or reports.
            No charts or sample data are fabricated.
          </p>
        </div>
      ) : (
        <div className="adaptive-artifact-content">
          {artifacts.length > 1 && (
            <nav className="adaptive-artifact-tabs" aria-label="Generated artifacts">
              {artifacts.map((artifact, index) => (
                <button
                  type="button"
                  key={artifact.id}
                  className={(activeArtifact?.id ?? '') === artifact.id ? 'is-active' : ''}
                  onClick={() => onSelectArtifact(artifact.id)}
                >
                  {artifact.title || `Result ${index + 1}`}
                </button>
              ))}
            </nav>
          )}
          {activeArtifact && (
            <article className="adaptive-artifact">
              {activeArtifact.reference && (
                <button
                  className="button button-secondary adaptive-artifact-save"
                  type="button"
                  onClick={() => onSave(activeArtifact.reference)}
                >
                  Save to dashboard
                </button>
              )}
              {activeArtifact.summary && <p className="adaptive-artifact-summary">{activeArtifact.summary}</p>}
              {activeArtifact.charts.map((chart) => (
                <ChartCard
                  chart={chart}
                  key={`${chart.title}-${chart.x_field}-${chart.y_field}`}
                />
              ))}
              {activeArtifact.findings.length > 0 && (
                <section className="adaptive-report-findings" aria-label="Report findings">
                  <h4>Findings</h4>
                  {activeArtifact.findings.map((finding, index) => (
                    <p key={finding.id ?? finding.statement ?? index}>
                      {finding.statement ?? String(finding)}
                    </p>
                  ))}
                </section>
              )}
              {activeArtifact.warnings.map((warning, index) => (
                <p className="chat-warning" key={`${warning}-${index}`}>{warning}</p>
              ))}
            </article>
          )}
        </div>
      )}
    </section>
  )
}
