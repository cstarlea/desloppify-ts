interface LegacyBannerProps {
  href: string;
}

// Shown during the v1 -> v2 migration; nothing renders it any more.
export function LegacyBanner({ href }: LegacyBannerProps) {
  return (
    <div className="banner">
      This app moved. <a href={href}>Open the new version</a>
    </div>
  );
}
