import { useEffect, useState } from 'react';
import { A2uiSurface, createComponentImplementation, type ReactCatalogComponent } from '@a2ui/react';
import { Catalog, MessageProcessor, SpecVersion, type SurfaceModel } from '@a2ui/web_core/v1_0';
import { z } from 'zod';
import { ProposalChangeList, type ProposalChange } from './ProposalChanges';

const comparison = createComponentImplementation({
  name: 'ProposalComparison',
  schema: z.object({
    changes: z.array(z.object({ field: z.string(), before: z.string(), after: z.string() })).max(6),
    labels: z.object({ before: z.string(), after: z.string() }),
  }),
}, ({ props }) => {
  const { changes, labels } = props;
  if (!changes || !labels || typeof labels.before !== 'string' || typeof labels.after !== 'string') {
    throw new Error('Incomplete proposal preview');
  }
  return <ProposalChangeList labels={{ before: labels.before, after: labels.after }} changes={changes.map(({ field, before, after }) => {
    if (typeof field !== 'string' || typeof before !== 'string' || typeof after !== 'string') throw new Error('Invalid proposal preview');
    return { field, before, after };
  })} />;
});
const catalog = new Catalog<ReactCatalogComponent>('https://lorelychat.com/catalogs/proposal-comparison/v1', SpecVersion.V1_0, [comparison], []);

type Props = { changes: ProposalChange[]; labels: { before: string; after: string } };
type Preview = { source: Props; surface?: SurfaceModel<ReactCatalogComponent> };
export default function A2uiProposalChanges({ changes, labels }: Props) {
  const [preview, setPreview] = useState<Preview>();
  useEffect(() => {
    const processor = new MessageProcessor<ReactCatalogComponent>([catalog]);
    const surfaceId = 'proposalComparison';
    try {
      // The Zod 3 SDK must never validate user-controlled collections. This
      // browser-only adapter builds exactly one local node and bounded strings.
      if (changes.length > 6 || changes.some(({ field, before, after }) =>
        [field, before, after].some(value => typeof value !== 'string' || value.length > 221))) {
        throw new Error('Proposal preview outside local bounds');
      }
      processor.processMessages([
        { version: SpecVersion.V1_0, createSurface: { surfaceId, catalogId: catalog.id } },
        { version: SpecVersion.V1_0, updateComponents: { surfaceId, components: [
          { id: 'root', component: comparison.name, changes, labels },
        ] } },
      ]);
      setPreview({ source: { changes, labels }, surface: processor.model.getSurface(surfaceId) });
    } catch {
      setPreview({ source: { changes, labels } });
    }
    return () => processor.dispose();
  }, [changes, labels]);
  const surface = preview?.source.changes === changes && preview.source.labels === labels ? preview.surface : undefined;
  return surface ? <div data-agent-preview="a2ui"><A2uiSurface surface={surface} /></div> : <ProposalChangeList changes={changes} labels={labels} />;
}
