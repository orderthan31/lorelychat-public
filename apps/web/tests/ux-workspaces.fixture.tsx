import React,{Suspense,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {I18nProvider} from '../src/i18n/I18nProvider';
import {ModelSettingsView} from '../src/views/ModelSettings';
import {WorldSettingsListView} from '../src/views/WorldSettings';
import {CharactersListView} from '../src/views/Characters';
import {ConversationsListView} from '../src/views/Conversations';
import {StatisticsView} from '../src/views/Statistics';
import {CharacterAssetsPanel,type CharacterAssetDraft} from '../src/components/organisms/CharacterAssetsPanel';
import '../src/styles.css';

function Fixture(){
 const [draft,setDraft]=useState<CharacterAssetDraft>({});
 const [nav,setNav]=useState('');
 const [saves,setSaves]=useState(0);
 const kind=new URLSearchParams(location.search).get('view') || 'models';
 const assets=[{id:'asset-a',label:'Synthetic scene',image_url:'/synthetic.png',description:'Owned synthetic image metadata',enabled:true,priority:50,tags:['scene']}];
 return <main className="shell"><output data-navigation>{nav}</output><output hidden data-asset-saves>{saves}</output><Suspense fallback={<div role="status">fixture loading</div>}>
  {kind==='models'?<ModelSettingsView section="models"/>:kind==='worlds'?<WorldSettingsListView navigate={setNav} loadWorldSettings={()=>{}}/>:kind==='characters'?<CharactersListView navigate={setNav}/>:kind==='conversations'?<ConversationsListView navigate={setNav} deleteConversation={id=>setNav(`delete:${id}`)} allCharacters={Array.from({length:5},(_,i)=>({id:`cast-${i}`,name:`Synthetic cast ${i}`,avatar_url:i===0?'/synthetic.png':undefined}))} openConversation={id=>setNav(`/conversations/${id}`)} worldSettings={[{id:'world-a',title:'Synthetic world'}]} />:kind==='statistics'?<StatisticsView/>:<CharacterAssetsPanel character={{id:'synthetic-character',name:'Synthetic'}} draft={draft} setDraft={setDraft} assets={assets} onPatch={()=>setSaves(value=>value+1)}/>}
 </Suspense></main>;
}
const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}><I18nProvider><Fixture/></I18nProvider></QueryClientProvider>);
