/* Peças de desenho dos perfis de parlamentar (senador.html e deputado.html): o mesmo desenho nas duas Casas.
   Cada função devolve um trecho de HTML; o CSS é do estilo.css ("PAINÉIS DE PERFIL"). Nada aqui calcula dado: quem chama traz os números
   do registro oficial e a regra de cada um fica na página e no script que o gera. Texto passado por parâmetro (`rot`, `sub`, `nome`…) é
   escapado aqui; `leg` e `html` de `medidor` e `comoLer` são HTML já montado por quem chama (que escapa o que vem de dado).
   Sem cor de espectro, de partido ou de bloco: uma tinta só (a do texto) e a rampa de neutros do site, nos dois temas.
   Uso: <script src="paineis.js"></script> antes do script da página; `window.Paineis`. */
window.Paineis=(function(){
  'use strict';
  var NF=new Intl.NumberFormat('pt-BR');
  function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function pct1(x){ return (x*100).toFixed(1).replace('.',',')+'%'; }
  function pct0(x){ return Math.round(x*100)+'%'; }
  // com quantidade par, a média dos dois do meio
  function medianaDe(v){ var n=v.length; if(!n) return null; var o=v.slice().sort(function(a,b){ return a-b; }); return n%2?o[(n-1)/2]:(o[n/2-1]+o[n/2])/2; }
  function dataBR(s){ return s?s.slice(8,10)+'/'+s.slice(5,7)+'/'+s.slice(0,4):'—'; }
  function minus(t){ return t?t.charAt(0).toLowerCase()+t.slice(1):''; }
  // [[início, fim|null, causa|null]] → "01/02/2023 a hoje (licença)"
  function periodosTxt(ex){
    return ex.map(function(e){ return dataBR(e[0])+' a '+(e[1]?dataBR(e[1]):'hoje')+(e[2]?' ('+esc(minus(e[2]))+')':''); }).join('; ');
  }

  // O texto de enquadramento vem antes do número, mas recolhido: a um clique, não no meio do caminho. Nunca apagado.
  function comoLer(html){ return '<details class="como"><summary>Como ler</summary>'+html+'</details>'; }

  // Régua de 0 a 100 %: o ponto deste perfil, a faixa dos colegas de partido e o traço da mediana da Casa.
  // o = {v, lo, hi, med, rotMed, aria}
  function regua(o){
    function p(x){ return (Math.max(0,Math.min(1,x))*100).toFixed(1); }
    return '<div class="regua" role="img" aria-label="'+esc(o.aria||'')+'"><span class="trilha"></span>'+
      (o.lo!=null?'<span class="faixa" style="left:'+p(o.lo)+'%;width:'+Math.max(.8,(o.hi-o.lo)*100).toFixed(1)+'%"></span>':'')+
      (o.med!=null?'<span class="tique" style="left:'+p(o.med)+'%" title="'+esc((o.rotMed||'mediana')+': '+pct1(o.med))+'"></span>':'')+
      '<span class="marca" style="left:'+p(o.v)+'%"></span></div>';
  }
  // Um medidor: nome, valor grande, a frase que o explica e a régua. Sem `v`, mostra só o valor e a frase (ex.: "—" e a nota do motivo).
  // o = {nome, val, leg (HTML), v, lo, hi, med, rotMed, aria}
  function medidor(o){
    var rot=o.rotMed||'mediana';
    return '<div class="med"><div class="med-topo"><span class="med-nome">'+esc(o.nome)+'</span><span class="med-val">'+o.val+'</span></div>'+
      '<p class="med-leg">'+o.leg+'</p>'+
      (o.v==null?'':regua(o)+'<div class="regua-leg"><span>0%</span><span>100%</span></div>'+
        '<div class="chave"><span><i class="k-ele"></i>este perfil</span>'+
        (o.med!=null?'<span><i class="k-med"></i>'+esc(rot)+', '+pct1(o.med)+'</span>':'')+
        (o.lo!=null?'<span><i class="k-faixa"></i>colegas de partido, de '+pct0(o.lo)+' a '+pct0(o.hi)+'</span>':'')+'</div>')+'</div>';
  }
  // Barra segmentada: [[rótulo, n, cor]] (n zero não aparece). Cada segmento tem o valor no title e a legenda traz os números por escrito.
  function barra(segs){
    var vis=segs.filter(function(x){ return x[1]; }), tot=vis.reduce(function(a,x){ return a+x[1]; },0)||1;
    return '<div class="votos-barra" role="img" aria-label="'+esc(vis.map(function(x){ return x[0]+' '+x[1]; }).join(', '))+'">'+
      vis.map(function(x){ return '<i style="width:'+(x[1]/tot*100).toFixed(2)+'%;background:'+x[2]+'" title="'+esc(x[0])+': '+NF.format(x[1])+'"></i>'; }).join('')+'</div>'+
      '<div class="votos-chaves">'+vis.map(function(x){ return '<span><em style="background:'+x[2]+'"></em>'+esc(x[0])+' <b>'+NF.format(x[1])+'</b></span>'; }).join('')+'</div>';
  }
  // Tons da rampa de neutros para os segmentos de uma barra: o primeiro é o mais forte
  var TONS=['var(--rampa1)','var(--rampa3)','var(--rampa4)','var(--rampa5)','var(--rampa6)','var(--rampa7)'];

  // Blocos de números: [{n (texto já formatado), rot, sub, mini:{v, total}}]. `mini` desenha a proporção v de total ao lado do volume.
  function tiles(itens){
    return '<div class="tiles">'+itens.map(function(t){
      return '<div class="tile"><b>'+esc(t.n)+'</b><span class="rot">'+esc(t.rot)+'</span><small>'+esc(t.sub||'')+'</small>'+
        (t.mini?'<div class="mini" role="img" aria-label="'+esc(NF.format(t.mini.v)+' de '+NF.format(t.mini.total))+'"><i style="width:'+(t.mini.total?(t.mini.v/t.mini.total*100).toFixed(1):0)+'%"></i></div>':'')+'</div>';
    }).join('')+'</div>';
  }

  // Posição do partido no espectro, numa régua de 1 a 10 SEM cor (a cor do espectro é de ideologia, e estes perfis não a usam).
  // o = {score, ideX, partido, nota}: o número leva a fonte e "calculada por este site".
  function espectro(o){
    var q=o.score;
    return '<div class="espec"><p class="espec-t">'+(q!=null
      ?'<b>Posição do partido no espectro: '+q.toFixed(1).replace('.',',')+' de 10</b>'+(o.partido?' ('+esc(o.partido)+')':'')+'. Média das respostas da wave 2021 do Brazilian Legislative Survey, calculada por este site.'
      :'<b>Sem posição no espectro</b>'+(o.partido?' ('+esc(o.partido)+')':'')+': '+esc(o.nota||'sem posição na pesquisa')+'.')+'</p>'+
      (q!=null&&o.ideX!=null?'<div><div class="regua" role="img" aria-label="'+q.toFixed(1).replace('.',',')+' de 10, numa escala de 1 (esquerda) a 10 (direita)"><span class="trilha"></span><span class="marca" style="left:'+(o.ideX*100).toFixed(1)+'%"></span></div>'+
        '<div class="regua-leg"><span>1 · esquerda</span><span>10 · direita</span></div></div>':'')+'</div>';
  }

  // Linha do tempo: contorno = o mandato (ou a legislatura), cheio = em exercício, traço verde = hoje.
  // o = {mandatos:[{de, ate, titulo}], exercicios:[{de, ate|null, titulo}], hoje:'aaaa-mm-dd', aria}. O eixo vai do ano em que o primeiro
  // período começa ao ano em que o último termina (31/1/2031 é o fim do mandato de 2023, não o ano 2032).
  function linhaDoTempo(o){
    function dec(d){ return d?+d.slice(0,4)+((+d.slice(5,7)-1)*30.4+(+d.slice(8,10)-1))/365:null; }
    var hoje=dec(o.hoje);
    var de=o.mandatos.map(function(m){ return dec(m.de); }), ate=o.mandatos.map(function(m){ return dec(m.ate)||hoje; });
    var a0=Math.floor(Math.min.apply(null,de)), a1=Math.max(a0+1,Math.ceil(Math.max.apply(null,ate.concat([hoje]))-.2));
    function pos(x){ return (Math.max(a0,Math.min(a1,x))-a0)/(a1-a0)*100; }
    var mand=o.mandatos.map(function(m,i){
      return '<span class="tl-mand" style="left:'+pos(de[i]).toFixed(2)+'%;width:'+Math.max(.8,pos(ate[i])-pos(de[i])).toFixed(2)+'%" title="'+esc(m.titulo||'')+'"></span>'; }).join('');
    var ex=o.exercicios.map(function(e){ var i=dec(e.de), f=dec(e.ate)||hoje;
      return '<span class="tl-ex" style="left:'+pos(i).toFixed(2)+'%;width:'+Math.max(.6,pos(f)-pos(i)).toFixed(2)+'%" title="'+esc(e.titulo||'')+'"></span>'; }).join('');
    // rótulos: as pontas e, no meio, os múltiplos de 4 (ou 8) que ficam a pelo menos 16% de cada ponta, para não encostarem
    var passo=a1-a0>20?8:4, anos=[a0];
    for(var a=Math.ceil(a0/passo)*passo;a<a1;a+=passo){ var p=pos(a); if(p>=16&&p<=84) anos.push(a); }
    anos.push(a1);
    return '<div class="tl"><div class="tl-trilha" role="img" aria-label="'+esc(o.aria||'Linha do tempo')+'">'+mand+ex+
      (hoje>=a0&&hoje<=a1?'<span class="tl-hoje" style="left:'+pos(hoje).toFixed(2)+'%" title="hoje"></span>':'')+'</div>'+
      '<div class="tl-anos" aria-hidden="true">'+anos.map(function(y){ return '<span style="left:'+pos(y).toFixed(2)+'%">'+y+'</span>'; }).join('')+'</div>'+
      '<div class="tl-chave"><span><i class="c-mand"></i>'+esc(o.rotMandato||'mandato')+'</span><span><i class="c-ex"></i>em exercício</span><span><i class="c-hoje"></i>hoje</span></div></div>';
  }
  // Como votou, POR TEMA: uma linha por tema com o Sim e o Não (e a abstenção, se houve) JUNTOS — "Estado, justiça e segurança  37 / 27" —, para comparar
  // os dois lados no mesmo tema. A barrinha tem o comprimento proporcional ao total de votos no tema (relativo ao maior) e é repartida entre Sim, Não e
  // abstenção, com os tons da barra de votos do cartão. Primeiro por BLOCO (7, com o carimbo do bloco) e, num "Por categoria" recolhido, por categoria (27).
  // Os números são contagens do registro oficial de voto; o tema é a classificação deste site (temas.py, pela ementa), nunca da Casa. Uma lei com mais de
  // um tema conta em cada um (a soma passa do total); "Sem categoria" e "Outras votações" vêm no fim, tracejadas, nunca somem.
  // o = {tax (taxonomia.json), unidade ('votações'), href (opcional: (nivel 'b'|'c'|'s'|'o', índice) → endereço da página "quais leis"), linhas:[{rot, k (tom de TONS), n (total do tipo), d:{b:[7], c:[27], s (sem categoria), o (matéria sem tema)}}], nota (HTML)}
  function votosTema(o){
    var tax=o.tax, T=window.TemaIcone, un=o.unidade||'votações';
    // as colunas: Sim e Não sempre; a abstenção só se este perfil teve alguma
    var cols=(o.linhas||[]).filter(function(l){ return l.d&&(l.n>0||l.rot==='Sim'||l.rot==='Não'); });
    if(!tax||!cols.length||!cols.some(function(l){ return l.n>0; })) return '';
    function soma(v){ return v.reduce(function(a,x){ return a+x; },0); }
    function num(vs){ return vs.map(function(v,i){ return (i?'<span>/</span>':'')+'<b>'+NF.format(v)+'</b>'; }).join(''); }
    function rotulo(vs){ return cols.map(function(c,i){ return c.rot+' '+NF.format(vs[i]); }).join(', '); }
    // vs = quanto de cada coluna neste tema; max = o maior total entre os temas (escala das barrinhas)
    // `href` (opcional, de o.href(nivel, índice)): a linha vira um link para a página que lista as leis daquele tema com o voto de cada uma
    function linha(nome,ic,vs,max,nulo,dica,href){
      var tot=soma(vs), tag=href?'a':'div';
      return '<'+tag+(href?' href="'+esc(href)+'"':'')+' class="vt-t'+(href?' vt-ir-link':'')+(nulo?' nulo':'')+'" title="'+esc((dica||(nome+': '+rotulo(vs)+' ('+un+')'))+(href?' · ver as leis':''))+'">'+
        '<div class="vt-nome">'+ic+'<span>'+esc(nome)+'</span></div>'+
        '<div class="vt-bar" role="img" aria-label="'+esc(rotulo(vs))+'"><span class="vt-fill" style="width:'+(max?(tot/max*100).toFixed(1):0)+'%">'+
          vs.map(function(v,i){ return v?'<i style="width:'+(v/tot*100).toFixed(2)+'%;background:'+TONS[cols[i].k]+'"></i>':''; }).join('')+'</span></div>'+
        '<div class="vt-num">'+num(vs)+(href?'<i class="vt-ir" aria-hidden="true">›</i>':'')+'</div></'+tag+'>';
    }
    function lista(nivel){
      var nomes=nivel==='b'?tax.blocos.map(function(b){ return b.nome; }):tax.categorias, itens=[];
      nomes.forEach(function(n,i){ var vs=cols.map(function(c){ return c.d[nivel][i]; }); if(soma(vs)) itens.push([vs,i]); });
      itens.sort(function(a,b){ return soma(b[0])-soma(a[0])||a[1]-b[1]; });
      var sem=cols.map(function(c){ return c.d.s||0; }), outras=cols.map(function(c){ return c.d.o||0; });
      var max=Math.max.apply(null,itens.map(function(x){ return soma(x[0]); }).concat([soma(sem),soma(outras)]));
      return itens.map(function(x){
          var ic=T?(nivel==='b'?T.bloco(nomes[x[1]],36):'<span class="vt-ic">'+T.cat(nomes[x[1]],24)+'</span>'):'';
          return linha(nomes[x[1]],ic,x[0],max,false,null,o.href?o.href(nivel,x[1]):null);
        }).join('')+
        (soma(sem)?linha('Sem categoria','',sem,max,true,'Votações cuja ementa não casa com nenhuma regra de tema',o.href?o.href('s',0):null):'')+
        (soma(outras)?linha('Outras votações','',outras,max,true,'Votações cuja matéria não é proposição legislativa (requerimento, mensagem…): não têm tema',o.href?o.href('o',0):null):'');
    }
    var legenda='<div class="vt-leg" aria-hidden="true">'+cols.map(function(c){ return '<span><i style="background:'+TONS[c.k]+'"></i>'+esc(c.rot)+'</span>'; }).join('')+
      '<span class="tot">no total: '+num(cols.map(function(c){ return c.n; }))+' '+esc(un)+'</span></div>';
    return '<div class="vt"><div class="vt-cab"><h3>Como votou, por tema</h3><span class="kick">'+cols.map(function(c){ return esc(c.rot); }).join(' / ')+', por tema da lei votada</span></div>'+
      legenda+'<div class="vt-lista">'+lista('b')+'</div>'+
      '<details class="como"><summary>Por categoria</summary>'+legenda+'<div class="vt-lista">'+lista('c')+'</div></details>'+
      (o.nota?'<p class="med-leg">'+o.nota+'</p>':'')+'</div>';
  }
  // Etiqueta pequena ("titular", "em curso"); `on` a deixa na cor invertida.
  function etq(txt,on){ return '<span class="etq'+(on?' on':'')+'">'+esc(txt)+'</span>'; }

  return {NF:NF,esc:esc,pct1:pct1,pct0:pct0,medianaDe:medianaDe,dataBR:dataBR,minus:minus,periodosTxt:periodosTxt,comoLer:comoLer,regua:regua,
          medidor:medidor,barra:barra,TONS:TONS,tiles:tiles,espectro:espectro,linhaDoTempo:linhaDoTempo,votosTema:votosTema,etq:etq};
})();
