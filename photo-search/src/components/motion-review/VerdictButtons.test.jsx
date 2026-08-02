/**
 * The reject dome's confirm step.
 *
 * Reject used to be pure bookkeeping; it now drops the row and can delete a
 * working copy off disk. The confirm is the only thing standing between a
 * mis-click and a deleted file, so what's tested here is mostly that it holds:
 * the dome itself never removes anything, and Cancel is genuinely inert.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import VerdictButtons from './VerdictButtons'

function setup(props = {}) {
  const onRejectAndRemove = vi.fn().mockResolvedValue(undefined)
  const onRemoveOnly = vi.fn().mockResolvedValue(undefined)
  const onExport = vi.fn()
  render(
    <VerdictButtons
      videoId="vid1"
      currentVerdict={null}
      exportedAt={null}
      owned={true}
      sourceSizeBytes={412 * 1024 * 1024}
      onRejectAndRemove={onRejectAndRemove}
      onRemoveOnly={onRemoveOnly}
      onExport={onExport}
      exporting={false}
      exportResult={null}
      {...props}
    />
  )
  return { onRejectAndRemove, onRemoveOnly, onExport }
}

// A saved (= exported, = approved) video. Only in this state is the third
// action, "Remove from queue", on offer at all.
function setupSaved(props = {}) {
  return setup({ exportedAt: '2026-07-30T11:02:00', ...props })
}

// A promise we resolve by hand, so "while the request is in flight" is a state
// the test controls rather than races.
function deferred() {
  let resolve
  const promise = new Promise((r) => { resolve = r })
  return { promise, resolve }
}

const REJECT = { name: /reject/i }
const SAVE = { name: /save/i }
const GHOST = { name: /remove from queue/i }
const CONFIRM = { name: 'Remove' } // exact: neither dome nor the ghost button

const REJECT_COPY = /remove this video from the queue/i
const REMOVE_ONLY_COPY = /remove the local copy/i

describe('<VerdictButtons> reject confirm', () => {
  it('does not remove anything when the dome is clicked', () => {
    const { onRejectAndRemove } = setup()

    fireEvent.click(screen.getByRole('button', { name: /reject/i }))

    expect(onRejectAndRemove).not.toHaveBeenCalled()
    expect(screen.getByText(/remove this video from the queue/i)).toBeInTheDocument()
  })

  it('removes only after the confirm button', () => {
    const { onRejectAndRemove } = setup()

    fireEvent.click(screen.getByRole('button', { name: /reject/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))

    expect(onRejectAndRemove).toHaveBeenCalledTimes(1)
    expect(onRejectAndRemove).toHaveBeenCalledWith('vid1')
  })

  it('leaves everything alone on Cancel', () => {
    const { onRejectAndRemove } = setup()

    fireEvent.click(screen.getByRole('button', { name: /reject/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onRejectAndRemove).not.toHaveBeenCalled()
    expect(screen.queryByText(/remove this video from the queue/i)).toBeNull()
  })

  it('closes on Escape without removing', () => {
    const { onRejectAndRemove } = setup()

    fireEvent.click(screen.getByRole('button', { name: /reject/i }))
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(onRejectAndRemove).not.toHaveBeenCalled()
    expect(screen.queryByText(/remove this video from the queue/i)).toBeNull()
  })

  it('says what it will free for a working copy we made', () => {
    setup({ owned: true, sourceSizeBytes: 412 * 1024 * 1024 })

    fireEvent.click(screen.getByRole('button', { name: /reject/i }))

    expect(screen.getByText(/frees about 412 MB/i)).toBeInTheDocument()
  })

  it('says nothing will be deleted for a merely-referenced file', () => {
    // The spec's headline copy promises "never your original". For an external
    // source that is the WHOLE story, and the user should be told so before
    // they click a red button.
    setup({ owned: false, sourceSizeBytes: 900 * 1024 * 1024 })

    fireEvent.click(screen.getByRole('button', { name: /reject/i }))

    expect(screen.getByText(/no working copy to delete/i)).toBeInTheDocument()
    expect(screen.queryByText(/frees about/i)).toBeNull()
  })

  it('does not offer the confirm while an export is running', () => {
    const { onRejectAndRemove } = setup({ exporting: true })

    fireEvent.click(screen.getByRole('button', { name: /reject/i }))

    expect(onRejectAndRemove).not.toHaveBeenCalled()
    expect(screen.queryByText(/remove this video from the queue/i)).toBeNull()
  })
})

/**
 * "Remove from queue" — the third action, and the one that is easiest to get
 * catastrophically wrong.
 *
 * It looks like Reject (drops the row, frees the working copy) but it must NOT
 * behave like Reject: rejecting goes through /motion-review/decision, which is
 * the only thing that retracts a video's savings credit. This button is for a
 * video whose export already banked that credit, so it fires the removal alone.
 * Wiring it to onRejectAndRemove would silently claw back reclaimed bytes the
 * user was told they had kept — hence the two "never the other callback" tests.
 */
describe('<VerdictButtons> remove-from-queue availability', () => {
  it('is not offered for an unreviewed video', () => {
    setup()
    expect(screen.queryByRole('button', GHOST)).toBeNull()
  })

  it('is not offered for a merely-rejected video', () => {
    // A reject already drops the row, so there is nothing left to remove — and
    // offering it here would route a rejected video through the savings-keeping
    // path, which is exactly backwards.
    setup({ currentVerdict: 'reject' })
    expect(screen.queryByRole('button', GHOST)).toBeNull()
  })

  it('is offered once the video has been exported', () => {
    setupSaved()
    expect(screen.getByRole('button', GHOST)).toBeInTheDocument()
  })

  it('appears as soon as an export lands, without a remount', () => {
    // The parent folds exported_at into `videos` state after a successful
    // export; the button has to show up on that re-render.
    const { rerender } = render(
      <VerdictButtons
        videoId="vid1"
        currentVerdict={null}
        exportedAt={null}
        owned={true}
        sourceSizeBytes={1024}
        onRejectAndRemove={vi.fn()}
        onRemoveOnly={vi.fn()}
        onExport={vi.fn()}
        exporting={false}
        exportResult={null}
      />
    )
    expect(screen.queryByRole('button', GHOST)).toBeNull()

    rerender(
      <VerdictButtons
        videoId="vid1"
        currentVerdict={null}
        exportedAt={'2026-07-30T11:02:00'}
        owned={true}
        sourceSizeBytes={1024}
        onRejectAndRemove={vi.fn()}
        onRemoveOnly={vi.fn()}
        onExport={vi.fn()}
        exporting={false}
        exportResult={null}
      />
    )
    expect(screen.getByRole('button', GHOST)).toBeInTheDocument()
  })
})

describe('<VerdictButtons> remove-from-queue confirm', () => {
  it('removes nothing when the ghost button is clicked', () => {
    const { onRemoveOnly, onRejectAndRemove } = setupSaved()

    fireEvent.click(screen.getByRole('button', GHOST))

    expect(onRemoveOnly).not.toHaveBeenCalled()
    expect(onRejectAndRemove).not.toHaveBeenCalled()
    expect(screen.getByText(REMOVE_ONLY_COPY)).toBeInTheDocument()
  })

  it('promises the export and the reclaimed total survive', () => {
    setupSaved()

    fireEvent.click(screen.getByRole('button', GHOST))

    expect(
      screen.getByText(
        /your export and reclaimed-space total stay.*this only frees the working file/i
      )
    ).toBeInTheDocument()
    // It must not borrow the reject wording, which promises nothing of the kind.
    expect(screen.queryByText(REJECT_COPY)).toBeNull()
  })

  it('calls onRemoveOnly with the video id after the confirm', () => {
    const { onRemoveOnly } = setupSaved()

    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.click(screen.getByRole('button', CONFIRM))

    expect(onRemoveOnly).toHaveBeenCalledTimes(1)
    expect(onRemoveOnly).toHaveBeenCalledWith('vid1')
  })

  it('never records a verdict when removing from the queue', () => {
    // The whole point of this action: no /motion-review/decision call, so the
    // savings credit the export earned is not retracted.
    const { onRemoveOnly, onRejectAndRemove } = setupSaved()

    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.click(screen.getByRole('button', CONFIRM))

    expect(onRejectAndRemove).not.toHaveBeenCalled()
    expect(onRemoveOnly).toHaveBeenCalledTimes(1)
  })

  it('still rejects through onRejectAndRemove on a saved video', () => {
    // The mirror of the test above: with both actions on screen, Reject must
    // keep its teeth and must not quietly become the savings-keeping variant.
    const { onRemoveOnly, onRejectAndRemove } = setupSaved()

    fireEvent.click(screen.getByRole('button', REJECT))
    fireEvent.click(screen.getByRole('button', CONFIRM))

    expect(onRejectAndRemove).toHaveBeenCalledTimes(1)
    expect(onRejectAndRemove).toHaveBeenCalledWith('vid1')
    expect(onRemoveOnly).not.toHaveBeenCalled()
  })

  it('leaves everything alone on Cancel', () => {
    const { onRemoveOnly } = setupSaved()

    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onRemoveOnly).not.toHaveBeenCalled()
    expect(screen.queryByText(REMOVE_ONLY_COPY)).toBeNull()
    expect(screen.getByRole('button', GHOST)).toBeInTheDocument()
  })

  it('closes on Escape without removing', () => {
    const { onRemoveOnly } = setupSaved()

    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(onRemoveOnly).not.toHaveBeenCalled()
    expect(screen.queryByText(REMOVE_ONLY_COPY)).toBeNull()
  })

  it('closes on an outside click without removing', () => {
    const { onRemoveOnly } = setupSaved()

    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.mouseDown(document.body)

    expect(onRemoveOnly).not.toHaveBeenCalled()
    expect(screen.queryByText(REMOVE_ONLY_COPY)).toBeNull()
  })

  it('stays open when the click lands inside the confirm', () => {
    const { onRemoveOnly } = setupSaved()

    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.mouseDown(screen.getByText(REMOVE_ONLY_COPY))

    expect(screen.getByText(REMOVE_ONLY_COPY)).toBeInTheDocument()
    expect(onRemoveOnly).not.toHaveBeenCalled()
  })

  it('closes again when the ghost button is clicked a second time', () => {
    const { onRemoveOnly } = setupSaved()
    const ghost = screen.getByRole('button', GHOST)

    fireEvent.click(ghost)
    fireEvent.click(ghost)

    expect(screen.queryByText(REMOVE_ONLY_COPY)).toBeNull()
    expect(onRemoveOnly).not.toHaveBeenCalled()
  })

  it('says what it will free for a working copy we made', () => {
    setupSaved({ owned: true, sourceSizeBytes: 412 * 1024 * 1024 })

    fireEvent.click(screen.getByRole('button', GHOST))

    expect(screen.getByText(/frees about 412 MB/i)).toBeInTheDocument()
  })

  it('says nothing will be deleted for a merely-referenced file', () => {
    setupSaved({ owned: false, sourceSizeBytes: 900 * 1024 * 1024 })

    fireEvent.click(screen.getByRole('button', GHOST))

    expect(screen.getByText(/no working copy to delete/i)).toBeInTheDocument()
    expect(screen.queryByText(/frees about/i)).toBeNull()
  })
})

describe('<VerdictButtons> the two confirms share one slot', () => {
  // Both popovers are absolutely positioned into the same gap above the domes,
  // so two open at once would overlap into unreadable mush — and, worse, would
  // show two identically-labelled "Remove" buttons wired to opposite actions.
  it('opening the remove-from-queue confirm closes the reject confirm', () => {
    setupSaved()

    fireEvent.click(screen.getByRole('button', REJECT))
    expect(screen.getByText(REJECT_COPY)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', GHOST))

    expect(screen.queryByText(REJECT_COPY)).toBeNull()
    expect(screen.getByText(REMOVE_ONLY_COPY)).toBeInTheDocument()
    expect(screen.getAllByRole('button', CONFIRM)).toHaveLength(1)
  })

  it('opening the reject confirm closes the remove-from-queue confirm', () => {
    setupSaved()

    fireEvent.click(screen.getByRole('button', GHOST))
    expect(screen.getByText(REMOVE_ONLY_COPY)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', REJECT))

    expect(screen.queryByText(REMOVE_ONLY_COPY)).toBeNull()
    expect(screen.getByText(REJECT_COPY)).toBeInTheDocument()
    expect(screen.getAllByRole('button', CONFIRM)).toHaveLength(1)
  })

  it('confirming after a swap fires only the action that is on screen', () => {
    // The dangerous sequence: open Reject, change your mind, open Remove from
    // queue, confirm. Only the visible action may run.
    const { onRemoveOnly, onRejectAndRemove } = setupSaved()

    fireEvent.click(screen.getByRole('button', REJECT))
    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.click(screen.getByRole('button', CONFIRM))

    expect(onRemoveOnly).toHaveBeenCalledTimes(1)
    expect(onRejectAndRemove).not.toHaveBeenCalled()
  })

  it('Escape closes whichever confirm is open', () => {
    setupSaved()

    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByText(REMOVE_ONLY_COPY)).toBeNull()

    fireEvent.click(screen.getByRole('button', REJECT))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByText(REJECT_COPY)).toBeNull()
  })
})

describe('<VerdictButtons> in-flight states', () => {
  it('shows "removing…" and frees up again once the removal resolves', async () => {
    const { promise, resolve } = deferred()
    const onRemoveOnly = vi.fn(() => promise)
    setupSaved({ onRemoveOnly })

    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.click(screen.getByRole('button', CONFIRM))

    expect(screen.getByRole('button', { name: /removing/i })).toBeInTheDocument()
    // The confirm gets out of the way as soon as the work starts.
    expect(screen.queryByText(REMOVE_ONLY_COPY)).toBeNull()

    await act(async () => { resolve() })

    await waitFor(() =>
      expect(screen.getByRole('button', GHOST)).toBeInTheDocument()
    )
    expect(screen.queryByRole('button', { name: /removing/i })).toBeNull()
  })

  it('is retryable after a removal that did not remove anything', async () => {
    // Models MotionReviewApp.removeOnly's real failure shape: it catches the
    // error itself, reports it in the status line and RESOLVES, leaving the row
    // in place. So the button must come back clickable rather than stay stuck
    // on "removing…" with the video still in the queue.
    const { promise, resolve } = deferred()
    const onRemoveOnly = vi.fn(() => promise)
    setupSaved({ onRemoveOnly })

    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.click(screen.getByRole('button', CONFIRM))
    await act(async () => { resolve() })

    await waitFor(() =>
      expect(screen.getByRole('button', GHOST)).not.toBeDisabled()
    )
    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.click(screen.getByRole('button', CONFIRM))
    expect(onRemoveOnly).toHaveBeenCalledTimes(2)
  })

  it('disables both domes while a removal is in flight', async () => {
    const { promise, resolve } = deferred()
    const onRemoveOnly = vi.fn(() => promise)
    const { onExport, onRejectAndRemove } = setupSaved({ onRemoveOnly })

    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.click(screen.getByRole('button', CONFIRM))

    expect(screen.getByRole('button', REJECT)).toBeDisabled()
    expect(screen.getByRole('button', SAVE)).toBeDisabled()

    // …and the disable is real, not just cosmetic.
    fireEvent.click(screen.getByRole('button', SAVE))
    fireEvent.click(screen.getByRole('button', REJECT))
    expect(onExport).not.toHaveBeenCalled()
    expect(onRejectAndRemove).not.toHaveBeenCalled()
    expect(screen.queryByText(REJECT_COPY)).toBeNull()

    await act(async () => { resolve() })
  })

  it('does not fire a second removal from a double-confirm', async () => {
    const { promise, resolve } = deferred()
    const onRemoveOnly = vi.fn(() => promise)
    setupSaved({ onRemoveOnly })

    fireEvent.click(screen.getByRole('button', GHOST))
    fireEvent.click(screen.getByRole('button', CONFIRM))
    // The confirm is gone and the ghost button is disabled, so there is no
    // second click to be had — assert that rather than trusting the styling.
    expect(screen.getByRole('button', { name: /removing/i })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /removing/i }))

    expect(onRemoveOnly).toHaveBeenCalledTimes(1)

    await act(async () => { resolve() })
  })

  it('disables the ghost button while a reject is in flight', async () => {
    const { promise, resolve } = deferred()
    const onRejectAndRemove = vi.fn(() => promise)
    const { onRemoveOnly } = setupSaved({ onRejectAndRemove })

    fireEvent.click(screen.getByRole('button', REJECT))
    fireEvent.click(screen.getByRole('button', CONFIRM))

    expect(screen.getByRole('button', GHOST)).toBeDisabled()
    fireEvent.click(screen.getByRole('button', GHOST))
    expect(onRemoveOnly).not.toHaveBeenCalled()
    expect(screen.queryByText(REMOVE_ONLY_COPY)).toBeNull()

    await act(async () => { resolve() })
  })

  it('does not offer the remove-from-queue confirm while an export is running', () => {
    const { onRemoveOnly } = setupSaved({ exporting: true })

    fireEvent.click(screen.getByRole('button', GHOST))

    expect(onRemoveOnly).not.toHaveBeenCalled()
    expect(screen.queryByText(REMOVE_ONLY_COPY)).toBeNull()
  })
})
