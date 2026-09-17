/* Edition 04: card artwork and lightweight optional reference overlays. */
'use strict';
window.ESTATE_ART_URLS=window.ESTATE_ART_URLS||{mansion:'assets/mansion-card.webp',greenhouse:'assets/greenhouse-card.webp',tomb:'assets/tomb-card.webp',barn:'assets/barn-card.webp',upperGarden:'assets/upper-garden-card.webp',lowerGarden:'assets/lower-garden-card.webp'};
// These oblique illustrations are used only in detail cards, never on the map.
window.ESTATE_LANDMARKS={'way/189853023':{asset:'mansion'},'way/189853024':{asset:'greenhouse'},'way/334219851':{asset:'tomb'},'way/232605898':{asset:'barn'}};
window.createEstateRenderer=pane=>L.canvas({pane,padding:.1,tolerance:3});
