declare module 'printagentx/PhotoSeries' {
  import type { ComponentType } from 'react';
  export interface PhotoSeriesProps {
    initialFolder?: string;
    defaultFolder?: string;
    agentBaseUrl?: string;
    defaultN?: number;
    showDocsButton?: boolean;
    initialShowDocs?: boolean;
    docsUrl?: string;
    storageKey?: string | null;
    syncUrlQuery?: boolean;
    embedded?: boolean;
    className?: string;
    onJobComplete?: (job: any) => void;
    onExportComplete?: (state: any) => void;
  }
  export const PhotoSeriesSection: ComponentType<PhotoSeriesProps>;
  export default PhotoSeriesSection;
}

declare module 'remote_photo_series/PhotoSeries' {
  export * from 'printagentx/PhotoSeries';
  export { default } from 'printagentx/PhotoSeries';
}
