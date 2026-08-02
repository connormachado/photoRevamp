/**
 * The reject dome's confirm step.
 *
 * Reject used to be pure bookkeeping; it now drops the row and can delete a
 * working copy off disk. The confirm is the only thing standing between a
 * mis-click and a deleted file, so what's tested here is mostly that it holds:
 * the dome itself never removes anything, and Cancel is genuinely inert.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import VerdictButtons from './VerdictButtons'

function setup(props = {}) {
  const onRejectAndRemove = vi.fn().mockResolvedValue(undefined)
  render(
    <VerdictButtons
      videoId="vid1"
      currentVerdict={null}
      exportedAt={null}
      owned={true}
      sourceSizeBytes={412 * 1024 * 1024}
      onRejectAndRemove={onRejectAndRemove}
      onExport={() => {}}
      exporting={false}
      exportResult={null}
      {...props}
    />
  )
  return { onRejectAndRemove }
}

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
